import os
from celery import Celery

broker = os.getenv("CELERY_BROKER_URL", "amqp://guest:guest@rabbitmq:5672//")
celery = Celery("client_manager_uploads", broker=broker)
# Simple config: no result backend required for PoC
celery.conf.update(task_acks_late=True, worker_prefetch_multiplier=1)
