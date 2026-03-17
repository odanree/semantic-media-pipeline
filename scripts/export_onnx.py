"""
Export CLIP ViT-L-14 visual encoder to ONNX and apply INT8 dynamic quantization.

Outputs (in --output-dir, default: models/):
  clip-vit-l-14-visual.onnx       — FP32 ONNX export
  clip-vit-l-14-visual-int8.onnx  — INT8 quantized (dynamic)

Also benchmarks FP32 PyTorch vs FP32 ONNX vs INT8 ONNX and prints a summary table.

Usage:
  python scripts/export_onnx.py
  python scripts/export_onnx.py --model clip-ViT-B-32 --output-dir models/ --bench-images path/to/jpgs/
"""

import argparse
import os
import time
from pathlib import Path

import numpy as np
import torch
import torch.nn as nn


# ---------------------------------------------------------------------------
# Visual encoder wrapper
# ---------------------------------------------------------------------------

class _CLIPVisualEncoder(nn.Module):
    """Wraps CLIP vision_model + visual_projection + L2 norm into a single exportable module."""

    def __init__(self, clip_model):
        super().__init__()
        self.vision_model = clip_model.vision_model
        self.visual_projection = clip_model.visual_projection

    def forward(self, pixel_values: torch.Tensor) -> torch.Tensor:
        outputs = self.vision_model(pixel_values=pixel_values)
        pooled = outputs.pooler_output
        embeds = self.visual_projection(pooled)
        return embeds / embeds.norm(dim=-1, keepdim=True)


# ---------------------------------------------------------------------------
# Export
# ---------------------------------------------------------------------------

def export_onnx(model_name: str, output_dir: Path) -> Path:
    """Export CLIP visual encoder to FP32 ONNX. Returns path to .onnx file."""
    from sentence_transformers import SentenceTransformer

    print(f"Loading {model_name} on CPU...")
    st_model = SentenceTransformer(model_name, device="cpu")
    clip_module = st_model._modules["0"]
    clip_model = clip_module.model.cpu()
    processor = clip_module.processor

    encoder = _CLIPVisualEncoder(clip_model).eval()

    # Dummy input: 1 image, 3 channels, 224×224
    dummy_pixel_values = torch.zeros(1, 3, 224, 224)

    onnx_path = output_dir / f"{model_name.lower().replace('/', '-')}-visual.onnx"
    print(f"Exporting to {onnx_path} ...")

    with torch.no_grad():
        torch.onnx.export(
            encoder,
            (dummy_pixel_values,),
            str(onnx_path),
            input_names=["pixel_values"],
            output_names=["embeddings"],
            dynamic_axes={
                "pixel_values": {0: "batch_size"},
                "embeddings": {0: "batch_size"},
            },
            opset_version=17,
        )

    size_mb = onnx_path.stat().st_size / 1024 / 1024
    print(f"  FP32 ONNX: {size_mb:.1f} MB")
    return onnx_path, processor


def quantize_int8(fp32_path: Path) -> Path:
    """Apply INT8 dynamic quantization. Returns path to quantized model."""
    from onnxruntime.quantization import quantize_dynamic, QuantType

    int8_path = fp32_path.with_name(fp32_path.stem + "-int8.onnx")
    print(f"Quantizing to INT8 → {int8_path} ...")

    quantize_dynamic(
        str(fp32_path),
        str(int8_path),
        weight_type=QuantType.QInt8,
    )

    size_mb = int8_path.stat().st_size / 1024 / 1024
    print(f"  INT8 ONNX: {size_mb:.1f} MB")
    return int8_path


# ---------------------------------------------------------------------------
# Benchmark
# ---------------------------------------------------------------------------

def _load_bench_images(bench_dir: str, processor, n: int = 20):
    """Load up to n images from bench_dir, preprocessed to pixel_values."""
    from PIL import Image

    paths = list(Path(bench_dir).glob("*.jpg"))[:n] + list(Path(bench_dir).glob("*.png"))[:n]
    paths = paths[:n]
    if not paths:
        raise FileNotFoundError(f"No jpg/png images found in {bench_dir}")

    images = [Image.open(p).convert("RGB") for p in paths]
    inputs = processor(images=images, return_tensors="pt")
    return inputs["pixel_values"]  # (n, 3, 224, 224)


def _dummy_pixel_values(n: int = 20) -> torch.Tensor:
    return torch.rand(n, 3, 224, 224)


def benchmark(model_name: str, fp32_path: Path, int8_path: Path,
              processor, bench_images_dir: str = None, n_runs: int = 5) -> None:
    """Benchmark FP32 PyTorch vs FP32 ONNX vs INT8 ONNX."""
    import onnxruntime as ort
    from sentence_transformers import SentenceTransformer

    print(f"\nBenchmarking (n_runs={n_runs}, ~20 images each)...")

    if bench_images_dir:
        pixel_values = _load_bench_images(bench_images_dir, processor)
        print(f"  Using {pixel_values.shape[0]} real images from {bench_images_dir}")
    else:
        pixel_values = _dummy_pixel_values()
        print("  Using random pixel_values (no --bench-images provided)")

    n_images = pixel_values.shape[0]

    # --- FP32 PyTorch ---
    st_model = SentenceTransformer(model_name, device="cpu")
    clip_module = st_model._modules["0"]
    encoder = _CLIPVisualEncoder(clip_module.model.cpu()).eval()

    pt_times = []
    with torch.no_grad():
        for _ in range(n_runs):
            t0 = time.perf_counter()
            ref_embeds = encoder(pixel_values).numpy()
            pt_times.append(time.perf_counter() - t0)
    pt_ms = np.mean(pt_times) * 1000

    # --- FP32 ONNX ---
    sess_fp32 = ort.InferenceSession(str(fp32_path), providers=["CPUExecutionProvider"])
    fp32_times = []
    for _ in range(n_runs):
        t0 = time.perf_counter()
        fp32_embeds = sess_fp32.run(None, {"pixel_values": pixel_values.numpy()})[0]
        fp32_times.append(time.perf_counter() - t0)
    fp32_ms = np.mean(fp32_times) * 1000

    # --- INT8 ONNX ---
    sess_int8 = ort.InferenceSession(str(int8_path), providers=["CPUExecutionProvider"])
    int8_times = []
    for _ in range(n_runs):
        t0 = time.perf_counter()
        int8_embeds = sess_int8.run(None, {"pixel_values": pixel_values.numpy()})[0]
        int8_times.append(time.perf_counter() - t0)
    int8_ms = np.mean(int8_times) * 1000

    # --- Accuracy: cosine similarity drift vs FP32 PyTorch reference ---
    def _cos_sim(a, b):
        a = a / np.linalg.norm(a, axis=-1, keepdims=True)
        b = b / np.linalg.norm(b, axis=-1, keepdims=True)
        return float(np.mean(np.sum(a * b, axis=-1)))

    fp32_sim = _cos_sim(ref_embeds, fp32_embeds)
    int8_sim = _cos_sim(ref_embeds, int8_embeds)

    # --- Model sizes ---
    pt_size = sum(p.numel() * p.element_size() for p in encoder.parameters()) / 1024 / 1024
    fp32_size = fp32_path.stat().st_size / 1024 / 1024
    int8_size = int8_path.stat().st_size / 1024 / 1024

    # --- Print table ---
    print(f"\n{'':30} {'FP32 PyTorch':>14} {'FP32 ONNX':>12} {'INT8 ONNX':>12}")
    print("-" * 72)
    print(f"{'Model size (MB)':30} {pt_size:>14.1f} {fp32_size:>12.1f} {int8_size:>12.1f}")
    print(f"{'Latency ms / batch ({n_images} imgs)':30} {pt_ms:>14.1f} {fp32_ms:>12.1f} {int8_ms:>12.1f}")
    print(f"{'Latency ms / image':30} {pt_ms/n_images:>14.2f} {fp32_ms/n_images:>12.2f} {int8_ms/n_images:>12.2f}")
    print(f"{'Speedup vs FP32 PyTorch':30} {'1.00×':>14} {pt_ms/fp32_ms:>11.2f}× {pt_ms/int8_ms:>11.2f}×")
    print(f"{'Cosine sim vs PT reference':30} {'1.0000':>14} {fp32_sim:>12.4f} {int8_sim:>12.4f}")
    print()

    if int8_sim < 0.99:
        print(f"WARNING: INT8 cosine similarity {int8_sim:.4f} < 0.99 — accuracy loss may be noticeable.")
    else:
        print(f"INT8 accuracy: {int8_sim:.4f} cosine sim vs FP32 — within acceptable range.")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Export CLIP to ONNX + INT8 quantization")
    parser.add_argument("--model", default=os.getenv("CLIP_MODEL_NAME", "clip-ViT-L-14"),
                        help="SentenceTransformer model name (default: clip-ViT-L-14)")
    parser.add_argument("--output-dir", default="models",
                        help="Directory to write .onnx files (default: models/)")
    parser.add_argument("--bench-images", default=None,
                        help="Optional directory of jpg/png images for realistic benchmark")
    parser.add_argument("--skip-benchmark", action="store_true",
                        help="Skip benchmark after export")
    parser.add_argument("--n-runs", type=int, default=5,
                        help="Number of benchmark runs to average (default: 5)")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    fp32_path, processor = export_onnx(args.model, output_dir)
    int8_path = quantize_int8(fp32_path)

    print(f"\nExport complete:")
    print(f"  FP32: {fp32_path}")
    print(f"  INT8: {int8_path}")
    print(f"\nTo use in the worker, set:")
    print(f"  CLIP_BACKEND=onnx")
    print(f"  CLIP_ONNX_MODEL_PATH={int8_path.absolute()}")

    if not args.skip_benchmark:
        benchmark(args.model, fp32_path, int8_path, processor,
                  bench_images_dir=args.bench_images, n_runs=args.n_runs)


if __name__ == "__main__":
    main()
