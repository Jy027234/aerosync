with open('docker-compose.yml', 'rb') as f:
    content = f.read().decode('utf-8')
print(content[800:1600])
