with open('docker-compose.yml') as f:
    content = f.read()
print('has_worker:', 'worker:' in content)
print('has_uvicorn:', 'uvicorn' in content)
print('has_celery_cmd:', 'celery -A' in content)
print('len:', len(content))
