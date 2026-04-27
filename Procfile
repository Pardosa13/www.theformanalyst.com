web: gunicorn app:app --worker-class gevent --workers 2 --worker-connections 100 --max-requests 500 --max-requests-jitter 50 --timeout 500 --worker-tmp-dir /dev/shm
