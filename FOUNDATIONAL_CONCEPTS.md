# Foundational Concepts Reference

> Things I was already doing — mapped to their formal names.
> Built from real work on this project and interview prep (ERI AI/ML Engineer, March 2026).

---

## Transformer Models

**The question:** "Do you use transformer models?"  
**The honest answer:** Yes — everything modern is a transformer.

"ViT" in ViT-B-32 and ViT-L-14 literally stands for **Vision Transformer**.
The text encoder inside CLIP also follows the same attention architecture as BERT/GPT.

When I unfroze blocks 22-23 during fine-tuning, I was backpropagating through **transformer attention blocks** specifically.

| Model Type | Examples | Encoder/Decoder |
|---|---|---|
| Vision embeddings | ViT, CLIP vision | Encoder |
| Text embeddings | CLIP text, BERT | Encoder |
| Language generation | GPT, Mistral, Llama | Decoder |
| Multimodal | CLIP, SigLIP, Florence | Both |

All four rows apply to work I've done.

---

## Math & Linear Algebra

**Cosine similarity** — what the `0.2` threshold in `search.py` is measuring.

$$\cos(\theta) = \frac{A \cdot B}{\|A\| \|B\|}$$

When I noticed the 3-5% score gap with ViT-B-32, I was observing vectors clustered in the same angular neighborhood. ViT-L-14 gives ~18% gap because 768 dimensions gives the geometry more room to separate concepts.

**High-dimensional vector spaces** — 92,805 vectors at 512-dim live in $\mathbb{R}^{512}$. Upgrading to 768-dim or 1152-dim isn't just "bigger" — it's a richer geometry for concept separation.

**Matrix multiplication** — every forward pass through CLIP is a series of matrix multiplies through transformer layers. Fine-tuning = updating those weight matrices via gradient descent.

---

## Statistics

**Precision/recall tradeoff** — lowering the cosine threshold returns more results (higher recall), but more false positives (lower precision). I was already reasoning about this when tuning thresholds.

**R², p-value, paired t-test** — the ERI data integrity page showed R²=0.82, p<0.0001. These are:
- **R²** = proportion of variance explained by the model (0.82 = strong)
- **p<0.0001** = probability the result is due to chance (essentially zero)
- **Paired t-test** = comparing two measurements on the same subject (before/after)

**99.1% agreement** = ERI's inter-rater reliability metric — how often two independent measurements agree.

---

## ML Fundamentals

**Transfer learning** — every time I loaded a pretrained CLIP model and ran it on my data, that's transfer learning. Fine-tuning is transfer learning where you also update some weights.

**Embeddings** — dense vector representations of data. The entire pipeline converts media files into embedding vectors so similarity can be measured geometrically.

**Cross-modal alignment** — CLIP was trained to bring paired image+text vectors close in the same vector space. This is why text queries can retrieve images.

**Why prompt templates hurt niche terms** — "a photo of labubu" pushed the query vector toward the generic photography cluster. "labubu" stayed closer to the concept itself. CLIP was trained on image-caption pairs, not photography prompts — so templates help common concepts, hurt niche brand names.

**Inference vs. training** — running a model forward (no gradient updates) = inference. During fine-tuning, you switch on gradient computation for specific layers only (partial fine-tune). I ran inference on 92,805 files and fine-tuned on ~1,000+ labeled samples for 15 classes.

**Precision/recall in classification** — for the fine-tuning work:
- **Precision** = of the things the model said were class X, how many actually were?
- **Recall** = of all the actual class X items, how many did the model find?
- **F1** = harmonic mean of the two: $F1 = 2 \cdot \frac{P \cdot R}{P + R}$

**Why 100% val → 85% real-world** — val set was IID (same distribution as training). Real-world has out-of-distribution samples the model hasn't seen. The 15% gap is normal; closing it requires more diverse training data.

---

## Computer Science

**Hashing / Content-addressable storage** — the frame caching plan uses `file_hash` as the cache key. Same idea as Git's object model: identical content = identical hash = no re-processing needed.

**Producer/consumer pattern** — Celery workers consuming from a Redis queue. One of the most classic concurrent systems patterns. The pipeline is: ingest API (producer) → Redis queue → workers (consumers) → Qdrant + Postgres.

**Lazy loading / memoization** — `search.py` loads the CLIP model once on first request and reuses the in-memory instance. Avoids paying the model load cost on every query.

**Caching with TTL** — the `Cache-Control: public, max-age=86400` header on thumbnails. Browser caches the image for 24h, avoiding redundant ffmpeg calls. Classic cache invalidation tradeoff: freshness vs. performance.

**Streaming I/O** — SSE (Server-Sent Events) in LLM Local Assistant and ffmpeg `pipe:1` stdout for thumbnails are the same principle: don't buffer the full output, process/transmit token by token or frame by frame.

**Tempfile + cleanup pattern** — `tempfile.mkdtemp()` + `shutil.rmtree()` is a standard pattern for ephemeral work directories. Guarantees no disk leak even if the process crashes (with a try/finally).

---

## Interview Framing Tips

When asked "do you use X?" — think about whether X is an *architecture*, *technique*, or *tool*:

- "Transformer models" → architecture → CLIP, ViT, GPT all qualify
- "Embeddings" → technique → yes, the whole search pipeline
- "Vector databases" → tool → yes, Qdrant
- "Containerization" → technique → yes, Docker + Compose
- "Async processing" → technique → yes, Celery + Redis

The question is almost always asking if you understand the *concept*, not just whether you typed the word.
