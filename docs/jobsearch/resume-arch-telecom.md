# Resume — Arch Telecom AI Engineer

---

## Danh Le
Orange, CA | 714-567-1107 | dtle82@gmail.com | linkedin.com/in/dtle82 | github.com/odanree | danhle.net

---

## Summary

AI Engineer with production ML experience across computer vision, NLP, and semantic search — designed and shipped a full end-to-end pipeline from raw data ingestion through vector retrieval and LLM reasoning. Five years in wireless telecom at Ultra Mobile (Mint Mobile) with hands-on understanding of the subscriber lifecycle, promotional campaign performance, and customer conversion metrics that drive wireless retail decisions. Applies the same data rigor that powered e-commerce systems serving millions of subscribers to building AI models that work in the real world.

---

## Technical Skills

**AI / ML:** CLIP (multimodal embeddings), YOLO (object detection), Ollama/LLaMA 3.2, LangChain, RAGAS (evaluation), Transformer architectures, NLP, computer vision
**Languages:** Python, JavaScript / TypeScript, SQL, PHP
**Frameworks:** FastAPI, Celery, Next.js, React
**Databases:** PostgreSQL, Qdrant (vector DB), Redis
**MLOps:** Docker, GitHub Actions CI/CD, Nginx, Hetzner Cloud, AWS Lambda / API Gateway
**Data & APIs:** REST APIs, WebSockets, OpenAI API, Pandas, NumPy
**Testing & Evaluation:** RAGAS (faithfulness, answer relevancy, context precision/recall), Cypress E2E, Optimizely A/B

---

## Projects

### Lumen — Semantic Media Search Pipeline (Production)
`github.com/odanree/semantic-media-pipeline` | `lumen.danhle.net`

- Built a full AI pipeline: CLIP multimodal embeddings generate vector representations for 500GB+ of unstructured media (video, images, audio) ingested into Qdrant vector DB; Ollama LLaMA 3.2 reasons over retrieved frames to answer natural-language queries
- Implemented YOLO object detection for automated visual enrichment and an audio processing layer for speech detection and segment classification — covering all three modalities (text, image, voice)
- Deployed RAGAS evaluation harness with GitHub Actions CI/CD gate; pipeline fails build if faithfulness or answer relevancy metrics drop below defined thresholds
- Achieved sub-second search latency on CPU-only ARM cloud server; optimized Qdrant payload indexes for 10,500x filtering speedup on 1.88M vectors
- MLOps: Docker Compose, automated deployment pipeline, Nginx reverse proxy, Hetzner Cloud VPS

### LLM Local Assistant — VS Code Extension (v2.13.1)
- Shipped production VS Code extension with AI-powered code analysis; built intelligent safeguards for AI-generated file modifications
- Designed and implemented multi-model routing strategy for different analysis tasks

### AI Chatbot
- Dual-personality chatbot using Strategy Pattern; demonstrates prompt engineering and LLM integration fundamentals

---

## Experience

### Software Engineer 2 | Ultra Mobile (Mint Mobile) | Costa Mesa, CA | May 2022 – Jan 2026
- Built and maintained e-commerce systems serving millions of wireless subscribers on Mint Mobile and Ultra Mobile platforms — deep familiarity with wireless retail KPIs, subscriber acquisition, and churn dynamics
- Identified a JavaScript compatibility bug using Google Analytics funnel analysis; quantified $10,000/month revenue impact from conversion drop-off data to prioritize the fix with stakeholders
- Built Promotions Manager — configurable rules engine that cut campaign deployment from 3 sprints to 1 and enabled marketing to self-serve promotions year-round without engineering involvement
- Ran A/B tests (Optimizely) on checkout flows and promotional experiences; used conversion data and revenue analysis to drive product decisions
- Designed custom CMS architecture (ACF field groups) enabling non-technical teams to manage content independently; reduced mid-sprint engineering interruptions from legal and marketing changes

### UX Developer | Ultra Mobile | Costa Mesa, CA | Jan 2020 – May 2022
- Introduced Storybook component library; established component isolation standard that reduced UI regressions and shortened code review cycles across the team
- Built responsive subscriber acquisition landing pages and promotional experiences for Mint Mobile campaigns, including Ryan Reynolds celebrity marketing partnerships

### Lead Front-end Developer | Mobovida, LLC | Jan 2018 – Jan 2019
- Led front-end development for wireless accessories e-commerce platform with large SKU catalog
- Mentored junior developers on coding standards and architecture best practices

### Lead Web Developer | OneClickSolar.com | Aug 2017 – Jan 2018
- Achieved sub-2-second page load performance for solar e-commerce platform through systematic performance optimization

### Intermediate Programmer | Rebar Interactive | Dec 2016 – Jul 2017
- Delivered client-facing web applications in a digital agency environment across multiple simultaneous accounts

### System Administrator | e4Hats.com | Nov 2013 – Jul 2016
- Managed infrastructure for e-commerce operation; reduced system downtime by 80% through proactive monitoring and process improvements

---

## Education

LearningFuze Development Bootcamp 2016

---

## Notes for Application

- **Wireless angle is the differentiator**: Arch Telecom is a 200-store wireless retailer. Lead with it in the cover letter — you already know subscriber churn, plan performance, and what metrics wireless retail actually tracks.
- **Degree field**: If degree is not completed, omit Education section; your production AI work is the credentialing story.
- **TF/PyTorch gap**: CLIP and YOLO are transformer-based CV models. If asked about PyTorch specifically, mention that these models run on PyTorch under the hood and you've worked directly with their inference APIs.
- **Pandas/NumPy**: If asked, confirm you've used both in data processing scripts — this is standard for the pipeline work.
