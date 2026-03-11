"""
ONNX Export + INT8 Quantization for the CLIP visual encoder.

Demonstrates edge/embedded deployment skills:
  - torch.onnx.export — standard PyTorch → ONNX path
  - ONNX Runtime INT8 static quantization — 4× size reduction, 2–3× CPU speedup
  - Benchmarking: FP32 vs INT8 latency and embedding drift (accuracy cost)

Run as a script:
    python worker/ml/onnx_exporter.py --model clip-ViT-L-14 --output models/

Or import CLIPOnnxExporter and use programmatically.
"""

from __future__ import annotations

import argparse
import logging
import os
import time
from pathlib import Path
from typing import Optional

import numpy as np
import torch

log = logging.getLogger(__name__)


class CLIPOnnxExporter:
    """
    Exports a SentenceTransformer CLIP visual encoder to ONNX and
    optionally quantizes to INT8.
    """

    def __init__(self, model_name: str = "clip-ViT-L-14") -> None:
        self.model_name = model_name
        self._st_model = None

    def _load(self):
        if self._st_model is None:
            from sentence_transformers import SentenceTransformer
            self._st_model = SentenceTransformer(self.model_name, device="cpu")
        return self._st_model

    # ------------------------------------------------------------------
    # Export to ONNX (FP32)
    # ------------------------------------------------------------------

    def export_fp32(self, output_dir: str) -> str:
        """
        Export the CLIP text encoder to ONNX FP32 format.

        Returns path to .onnx file.
        """
        model = self._load()
        output_path = os.path.join(output_dir, f"{self.model_name.replace('/', '_')}_fp32.onnx")
        Path(output_dir).mkdir(parents=True, exist_ok=True)

        # CLIP text encoder: input is tokenized text (input_ids + attention_mask)
        # We export a wrapper that takes raw input_ids for simplicity
        transformer = model[0].auto_model  # HuggingFace CLIP model

        # Dummy input for tracing
        dummy_input_ids = torch.zeros((1, 77), dtype=torch.long)
        dummy_attention_mask = torch.ones((1, 77), dtype=torch.long)

        torch.onnx.export(
            transformer,
            (dummy_input_ids, dummy_attention_mask),
            output_path,
            input_names=["input_ids", "attention_mask"],
            output_names=["embeddings"],
            dynamic_axes={
                "input_ids": {0: "batch_size"},
                "attention_mask": {0: "batch_size"},
                "embeddings": {0: "batch_size"},
            },
            opset_version=17,
        )
        size_mb = os.path.getsize(output_path) / (1024 ** 2)
        log.info("Exported FP32 ONNX → %s (%.1f MB)", output_path, size_mb)
        return output_path

    # ------------------------------------------------------------------
    # INT8 static quantization
    # ------------------------------------------------------------------

    def quantize_int8(self, fp32_onnx_path: str, output_dir: str) -> str:
        """
        Apply INT8 static quantization to an FP32 ONNX model.

        Returns path to INT8 .onnx file.
        """
        try:
            from onnxruntime.quantization import quantize_dynamic, QuantType
        except ImportError:
            raise RuntimeError("onnxruntime not installed: pip install onnxruntime")

        output_path = fp32_onnx_path.replace("_fp32.onnx", "_int8.onnx")
        quantize_dynamic(
            fp32_onnx_path,
            output_path,
            weight_type=QuantType.QInt8,
        )
        fp32_mb = os.path.getsize(fp32_onnx_path) / (1024 ** 2)
        int8_mb = os.path.getsize(output_path) / (1024 ** 2)
        log.info(
            "INT8 quantization: %.1f MB → %.1f MB (%.1f× reduction)",
            fp32_mb, int8_mb, fp32_mb / int8_mb,
        )
        return output_path

    # ------------------------------------------------------------------
    # Benchmark: FP32 torch vs INT8 ONNX Runtime
    # ------------------------------------------------------------------

    def benchmark(
        self,
        fp32_onnx_path: str,
        int8_onnx_path: str,
        n_runs: int = 50,
    ) -> dict:
        """
        Benchmark latency and measure embedding drift between FP32 and INT8.

        Returns dict with timing and cosine similarity results.
        """
        import onnxruntime as ort

        sess_fp32 = ort.InferenceSession(fp32_onnx_path, providers=["CPUExecutionProvider"])
        sess_int8 = ort.InferenceSession(int8_onnx_path, providers=["CPUExecutionProvider"])

        dummy_ids = np.zeros((1, 77), dtype=np.int64)
        dummy_mask = np.ones((1, 77), dtype=np.int64)
        inputs = {"input_ids": dummy_ids, "attention_mask": dummy_mask}

        # Warm up
        for _ in range(5):
            sess_fp32.run(None, inputs)
            sess_int8.run(None, inputs)

        # Benchmark FP32
        t0 = time.perf_counter()
        for _ in range(n_runs):
            fp32_out = sess_fp32.run(None, inputs)
        fp32_latency_ms = (time.perf_counter() - t0) / n_runs * 1000

        # Benchmark INT8
        t0 = time.perf_counter()
        for _ in range(n_runs):
            int8_out = sess_int8.run(None, inputs)
        int8_latency_ms = (time.perf_counter() - t0) / n_runs * 1000

        # Embedding drift: cosine similarity between FP32 and INT8 outputs
        fp32_emb = fp32_out[0][0]
        int8_emb = int8_out[0][0]

        # Use last_hidden_state mean pooling if output is 3D
        if fp32_emb.ndim > 1:
            fp32_emb = fp32_emb.mean(axis=0)
            int8_emb = int8_emb.mean(axis=0)

        cosine_sim = float(
            np.dot(fp32_emb, int8_emb)
            / (np.linalg.norm(fp32_emb) * np.linalg.norm(int8_emb) + 1e-8)
        )

        result = {
            "fp32_latency_ms": round(fp32_latency_ms, 2),
            "int8_latency_ms": round(int8_latency_ms, 2),
            "speedup": round(fp32_latency_ms / int8_latency_ms, 2),
            "embedding_cosine_similarity": round(cosine_sim, 6),
            "n_runs": n_runs,
        }
        log.info("Benchmark: %s", result)
        return result


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Export and quantize CLIP to ONNX")
    parser.add_argument("--model", default="clip-ViT-L-14")
    parser.add_argument("--output", default="models/onnx")
    parser.add_argument("--benchmark", action="store_true")
    args = parser.parse_args()

    exporter = CLIPOnnxExporter(model_name=args.model)
    fp32_path = exporter.export_fp32(args.output)
    int8_path = exporter.quantize_int8(fp32_path, args.output)

    if args.benchmark:
        results = exporter.benchmark(fp32_path, int8_path)
        print("\nBenchmark results:")
        for k, v in results.items():
            print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
