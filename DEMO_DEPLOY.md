# Starting Evident for a demo

## Local (fastest)
```bash
source venv/bin/activate
export APP_MODE=local
uvicorn main:app --host 0.0.0.0 --port 8000
```
Open http://localhost:8000

## AWS ECS (cloud demo)
```bash
# Scale service back up (was set to 0 to save costs)
aws ecs update-service \
  --cluster evident-cluster \
  --service evident-service \
  --desired-count 1 \
  --region us-east-2

# Wait ~2 minutes then open your domain
```

## Shutting down after demo
```bash
aws ecs update-service \
  --cluster evident-cluster \
  --service evident-service \
  --desired-count 0 \
  --region us-east-2
```
This stops the Fargate task and billing.
Your data and config are preserved. Just scale back up next time.
