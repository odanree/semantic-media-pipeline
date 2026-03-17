"""
Fine-tune CLIP ViT-L-14 on image-caption pairs pulled from Qdrant.

Strategy: head-only by default (projection layers only) — appropriate for
small corpora (<5K pairs). Use --unfreeze-blocks 2 when you have 15K+ pairs.

At N=1636 pairs, expect ~0 retrieval improvement over base CLIP. The value
here is the training loop artifact, not the model weights.

Usage:
    python scripts/fine_tune_clip.py
    python scripts/fine_tune_clip.py --unfreeze-blocks 0 --epochs 10 --dry-run

Outputs:
    models/clip-lumen-ft/best.pt       — best checkpoint by validation loss
    models/clip-lumen-ft/metrics.json  — per-epoch loss curves
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import List, Optional, Tuple

import numpy as np
import torch
import torch.nn.functional as F
from PIL import Image
from torch.utils.data import DataLoader, Dataset

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Path translation (mirrors worker/tasks.py _translate_path)
# ---------------------------------------------------------------------------

def _translate_path(path: str) -> str:
    """Map Linux container paths → Windows native paths via LUMEN_PATH_MAP_N env vars."""
    import platform
    if platform.system() != "Windows":
        return path
    i = 0
    mappings = []
    while (m := os.environ.get(f"LUMEN_PATH_MAP_{i}")) is not None:
        linux_prefix, win_prefix = m.split(":", 1)
        mappings.append((linux_prefix, win_prefix))
        i += 1
    # Longest prefix first
    mappings.sort(key=lambda x: len(x[0]), reverse=True)
    for linux_prefix, win_prefix in mappings:
        if path.startswith(linux_prefix):
            return win_prefix + path[len(linux_prefix):]
    return path


# ---------------------------------------------------------------------------
# Data loading
# ---------------------------------------------------------------------------

def load_pairs_from_qdrant(
    collection: str,
    qdrant_host: str,
    qdrant_port: int,
    min_caption_len: int = 10,
    limit: Optional[int] = None,
) -> List[Tuple[str, str]]:
    """
    Scroll Qdrant for all points with a non-empty caption payload.
    Returns list of (native_file_path, caption) tuples.
    """
    from qdrant_client import QdrantClient

    client = QdrantClient(host=qdrant_host, port=qdrant_port, timeout=60)
    pairs = []
    offset = None

    log.info("Scrolling Qdrant collection '%s' for captioned points...", collection)
    while True:
        results, next_offset = client.scroll(
            collection_name=collection,
            scroll_filter=None,
            limit=500,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        for point in results:
            caption = (point.payload or {}).get("caption", "")
            file_path = (point.payload or {}).get("file_path", "")
            if not caption or len(caption) < min_caption_len or not file_path:
                continue
            native_path = _translate_path(file_path)
            pairs.append((native_path, caption))
            if limit and len(pairs) >= limit:
                log.info("Reached limit of %d pairs.", limit)
                return pairs

        if next_offset is None:
            break
        offset = next_offset

    log.info("Found %d captioned pairs.", len(pairs))
    return pairs


# ---------------------------------------------------------------------------
# Dataset
# ---------------------------------------------------------------------------

class CLIPPairDataset(Dataset):
    def __init__(self, pairs: List[Tuple[str, str]]):
        self.pairs = pairs

    def __len__(self):
        return len(self.pairs)

    def __getitem__(self, idx):
        return self.pairs[idx]  # (path, caption)


def _make_collate(processor):
    def collate(batch):
        paths, captions = zip(*batch)
        images, valid_captions = [], []
        for path, caption in zip(paths, captions):
            try:
                images.append(Image.open(path).convert("RGB"))
                valid_captions.append(caption)
            except Exception as e:
                log.debug("Skipping unreadable image %s: %s", path, e)
        if not images:
            return None
        inputs = processor(
            images=images,
            text=list(valid_captions),
            return_tensors="pt",
            padding=True,
            truncation=True,
            max_length=77,
        )
        return inputs
    return collate


# ---------------------------------------------------------------------------
# Loss
# ---------------------------------------------------------------------------

def clip_contrastive_loss(
    image_embeds: torch.Tensor,
    text_embeds: torch.Tensor,
    temperature: float = 0.07,
) -> torch.Tensor:
    """
    Symmetric InfoNCE / NT-Xent loss — the same objective CLIP was trained with.
    Diagonal of the similarity matrix = positive pairs.
    """
    image_embeds = F.normalize(image_embeds, dim=-1)
    text_embeds = F.normalize(text_embeds, dim=-1)
    logits = (image_embeds @ text_embeds.T) / temperature
    labels = torch.arange(len(image_embeds), device=image_embeds.device)
    loss_i2t = F.cross_entropy(logits, labels)
    loss_t2i = F.cross_entropy(logits.T, labels)
    return (loss_i2t + loss_t2i) / 2.0


# ---------------------------------------------------------------------------
# Freeze helpers
# ---------------------------------------------------------------------------

def configure_trainable_params(clip_model, unfreeze_blocks: int) -> int:
    """Freeze all params, then selectively unfreeze based on strategy."""
    for param in clip_model.parameters():
        param.requires_grad = False

    # Always train projection layers (head-only minimum)
    trainable = [clip_model.visual_projection, clip_model.text_projection]
    for module in trainable:
        for param in module.parameters():
            param.requires_grad = True

    if unfreeze_blocks > 0:
        vision_blocks = clip_model.vision_model.encoder.layers
        text_blocks = clip_model.text_model.encoder.layers
        for block in list(vision_blocks)[-unfreeze_blocks:]:
            for param in block.parameters():
                param.requires_grad = True
        for block in list(text_blocks)[-unfreeze_blocks:]:
            for param in block.parameters():
                param.requires_grad = True
        log.info("Unfrozen: projection layers + last %d vision/text blocks", unfreeze_blocks)
    else:
        log.info("Unfrozen: projection layers only (head-only)")

    n_trainable = sum(p.numel() for p in clip_model.parameters() if p.requires_grad)
    n_total = sum(p.numel() for p in clip_model.parameters())
    log.info("Trainable params: %s / %s (%.1f%%)",
             f"{n_trainable:,}", f"{n_total:,}", 100 * n_trainable / n_total)
    return n_trainable


# ---------------------------------------------------------------------------
# Train / eval loops
# ---------------------------------------------------------------------------

def run_epoch(clip_model, processor, loader, optimizer, device, training: bool) -> float:
    clip_model.train(training)
    total_loss = 0.0
    n_batches = 0

    with torch.set_grad_enabled(training):
        for batch in loader:
            if batch is None:
                continue

            pixel_values = batch["pixel_values"].to(device)
            input_ids = batch["input_ids"].to(device)
            attention_mask = batch.get("attention_mask")
            if attention_mask is not None:
                attention_mask = attention_mask.to(device)

            outputs = clip_model(
                pixel_values=pixel_values,
                input_ids=input_ids,
                attention_mask=attention_mask,
            )
            loss = clip_contrastive_loss(outputs.image_embeds, outputs.text_embeds)

            if training:
                optimizer.zero_grad()
                loss.backward()
                torch.nn.utils.clip_grad_norm_(
                    [p for p in clip_model.parameters() if p.requires_grad], max_norm=1.0
                )
                optimizer.step()

            total_loss += loss.item()
            n_batches += 1

    return total_loss / max(n_batches, 1)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Fine-tune CLIP on Qdrant image-caption pairs")
    parser.add_argument("--model", default=os.getenv("CLIP_MODEL_NAME", "clip-ViT-L-14"))
    parser.add_argument("--collection", default=os.getenv("QDRANT_COLLECTION_NAME", "media_vectors"))
    parser.add_argument("--qdrant-host", default=os.getenv("QDRANT_HOST", "localhost"))
    parser.add_argument("--qdrant-port", type=int, default=int(os.getenv("QDRANT_PORT", "6333")))
    parser.add_argument("--output-dir", default="models/clip-lumen-ft")
    parser.add_argument("--epochs", type=int, default=10)
    parser.add_argument("--batch-size", type=int, default=16)
    parser.add_argument("--lr", type=float, default=1e-5)
    parser.add_argument("--weight-decay", type=float, default=0.01)
    parser.add_argument("--warmup-steps", type=int, default=50)
    parser.add_argument("--val-split", type=float, default=0.2)
    parser.add_argument("--unfreeze-blocks", type=int, default=0,
                        help="Vision+text transformer blocks to unfreeze (0=head-only). "
                             "Use 0 for <5K pairs, 2 for 15K+ pairs.")
    parser.add_argument("--temperature", type=float, default=0.07)
    parser.add_argument("--early-stopping-patience", type=int, default=3)
    parser.add_argument("--device", default=None, help="cpu | cuda (default: auto)")
    parser.add_argument("--dry-run", action="store_true",
                        help="Load data and model only — no training")
    parser.add_argument("--limit", type=int, default=None,
                        help="Cap number of pairs loaded from Qdrant (for testing)")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # --- Load pairs ---
    pairs = load_pairs_from_qdrant(
        collection=args.collection,
        qdrant_host=args.qdrant_host,
        qdrant_port=args.qdrant_port,
        limit=args.limit,
    )
    if not pairs:
        log.error("No captioned pairs found in Qdrant. Run backfill_captions first.")
        sys.exit(1)

    log.info("Corpus: %d pairs | val_split=%.0f%% → train=%d val=%d",
             len(pairs), args.val_split * 100,
             int(len(pairs) * (1 - args.val_split)),
             int(len(pairs) * args.val_split))

    if len(pairs) < 100:
        log.warning("Only %d pairs — fine-tuning at this scale is unlikely to improve "
                    "retrieval quality over base CLIP.", len(pairs))

    # --- Load model ---
    from sentence_transformers import SentenceTransformer

    log.info("Loading %s...", args.model)
    st_model = SentenceTransformer(args.model, device="cpu")
    clip_module = st_model._modules["0"]
    clip_model = clip_module.model
    processor = clip_module.processor

    device = args.device or ("cuda" if torch.cuda.is_available() else "cpu")
    log.info("Using device: %s", device)
    clip_model = clip_model.to(device)

    configure_trainable_params(clip_model, args.unfreeze_blocks)

    if args.dry_run:
        log.info("Dry run — exiting before training.")
        return

    # --- Splits ---
    rng = np.random.default_rng(42)
    indices = rng.permutation(len(pairs)).tolist()
    split = int(len(pairs) * (1 - args.val_split))
    train_pairs = [pairs[i] for i in indices[:split]]
    val_pairs = [pairs[i] for i in indices[split:]]

    collate = _make_collate(processor)
    train_loader = DataLoader(CLIPPairDataset(train_pairs), batch_size=args.batch_size,
                              shuffle=True, collate_fn=collate, num_workers=0)
    val_loader = DataLoader(CLIPPairDataset(val_pairs), batch_size=args.batch_size,
                            shuffle=False, collate_fn=collate, num_workers=0)

    # --- Optimizer + scheduler ---
    trainable_params = [p for p in clip_model.parameters() if p.requires_grad]
    optimizer = torch.optim.AdamW(trainable_params, lr=args.lr,
                                  weight_decay=args.weight_decay)
    total_steps = args.epochs * len(train_loader)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer, T_max=total_steps - args.warmup_steps, eta_min=1e-7
    )

    def warmup_lambda(step):
        if step < args.warmup_steps:
            return step / max(args.warmup_steps, 1)
        return 1.0

    warmup_scheduler = torch.optim.lr_scheduler.LambdaLR(optimizer, warmup_lambda)

    # --- Training loop ---
    best_val_loss = float("inf")
    patience_counter = 0
    metrics = {"train_loss": [], "val_loss": [], "lr": []}

    log.info("Starting training: epochs=%d batch=%d lr=%g unfreeze_blocks=%d",
             args.epochs, args.batch_size, args.lr, args.unfreeze_blocks)

    for epoch in range(1, args.epochs + 1):
        t0 = time.perf_counter()

        train_loss = run_epoch(clip_model, processor, train_loader,
                               optimizer, device, training=True)
        val_loss = run_epoch(clip_model, processor, val_loader,
                             optimizer, device, training=False)

        current_lr = optimizer.param_groups[0]["lr"]
        elapsed = time.perf_counter() - t0

        metrics["train_loss"].append(train_loss)
        metrics["val_loss"].append(val_loss)
        metrics["lr"].append(current_lr)

        log.info("Epoch %2d/%d | train=%.4f val=%.4f | lr=%.2e | %.1fs",
                 epoch, args.epochs, train_loss, val_loss, current_lr, elapsed)

        # Step schedulers
        for _ in range(len(train_loader)):
            if optimizer.param_groups[0]["last_epoch"] < args.warmup_steps:
                warmup_scheduler.step()
            else:
                scheduler.step()

        # Checkpoint
        if val_loss < best_val_loss:
            best_val_loss = val_loss
            patience_counter = 0
            ckpt_path = output_dir / "best.pt"
            torch.save({
                "epoch": epoch,
                "model_state_dict": clip_model.state_dict(),
                "optimizer_state_dict": optimizer.state_dict(),
                "val_loss": val_loss,
                "train_loss": train_loss,
                "args": vars(args),
            }, ckpt_path)
            log.info("  ✓ Saved best checkpoint (val_loss=%.4f)", val_loss)
        else:
            patience_counter += 1
            if patience_counter >= args.early_stopping_patience:
                log.info("Early stopping at epoch %d (patience=%d)",
                         epoch, args.early_stopping_patience)
                break

    # --- Save metrics ---
    metrics_path = output_dir / "metrics.json"
    with open(metrics_path, "w") as f:
        json.dump(metrics, f, indent=2)

    print("\n" + "=" * 60)
    print(f"Training complete")
    print(f"  Best val loss : {best_val_loss:.4f}")
    print(f"  Checkpoint    : {output_dir / 'best.pt'}")
    print(f"  Metrics       : {metrics_path}")
    print(f"  Pairs used    : {len(pairs)} ({len(train_pairs)} train / {len(val_pairs)} val)")
    print()
    print("NOTE: With <5K pairs, base CLIP likely outperforms this fine-tuned model.")
    print("Run scripts/evaluate_retrieval.py to compare before deploying.")
    print("=" * 60)


if __name__ == "__main__":
    main()
