# Ingestion curl commands (lumen2 — port 8001)

Trigger ingestion for any mount. Each call returns a `task_id` you can poll.

## Mounts

| Docker path           | Windows path                     | .env var    |
|-----------------------|----------------------------------|-------------|
| /mnt/source/f-ltv     | F:/storage/Luxury TV             | L2_MEDIA_1  |
| /mnt/source/e         | E:/Unsorted                      | L2_MEDIA_2  |
| /mnt/source/d-4k-index| D:/4K Index                      | L2_MEDIA_3  |
| /mnt/source/c-index   | C:/Users/Danh/Downloads/C index  | L2_MEDIA_4  |
| /mnt/source/f-index   | F:/storage/F Index               | L2_MEDIA_5  |

## Ingest commands

```bash
# f-ltv (Luxury TV)
curl -s -X POST http://localhost:8001/api/ingest \
  -H "Content-Type: application/json" \
  -d '{"media_root": "/mnt/source/f-ltv"}'

# e (Unsorted)
curl -s -X POST http://localhost:8001/api/ingest \
  -H "Content-Type: application/json" \
  -d '{"media_root": "/mnt/source/e"}'

# d-4k-index
curl -s -X POST http://localhost:8001/api/ingest \
  -H "Content-Type: application/json" \
  -d '{"media_root": "/mnt/source/d-4k-index"}'

# c-index
curl -s -X POST http://localhost:8001/api/ingest \
  -H "Content-Type: application/json" \
  -d '{"media_root": "/mnt/source/c-index"}'

# f-index
curl -s -X POST http://localhost:8001/api/ingest \
  -H "Content-Type: application/json" \
  -d '{"media_root": "/mnt/source/f-index"}'
```

## Monitor task

```bash
curl -s http://localhost:8001/api/task/<task_id>
```

## Force re-ingest (re-resolves moved files)

Add `"force_relocate": true` to any command above:

```bash
curl -s -X POST http://localhost:8001/api/ingest \
  -H "Content-Type: application/json" \
  -d '{"media_root": "/mnt/source/f-index", "force_relocate": true}'
```
