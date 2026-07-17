# Docker demo

The root `Dockerfile` packages the synchronous ReleaseGuard API for local
demonstration and CI smoke testing. It is not a production isolation boundary
for untrusted code.

Build and start the image from the repository root:

```powershell
docker build --tag releaseguard-agent:local .
docker run --detach --name releaseguard-demo --publish 8000:8000 releaseguard-agent:local
.venv\Scripts\python.exe scripts\http_health_smoke.py --url http://127.0.0.1:8000/health
```

Review the clean sample inside the container:

```powershell
$body = @{project_path = "/app/sample_projects/clean_python_project"; include_pytest_execution = $false} | ConvertTo-Json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/reviews -ContentType "application/json" -Body $body
```

Stop the owned demo container:

```powershell
docker rm --force releaseguard-demo
```

The image:

- runs as UID 10001 rather than root;
- exposes port 8000 and defines a Docker health check;
- includes runtime dependencies, source, local rule knowledge, eval data, and
  sample projects;
- excludes `.env`, the local virtual environment, Git metadata, tests, caches,
  outputs, and project memory from the build context;
- makes no LLM or embedding request unless explicitly configured at runtime.

Linux uses the same `docker build`, `docker run`, and Python smoke commands.
The workflow in `.github/workflows/ci.yml` performs these steps on an Ubuntu
runner without publishing an image.
