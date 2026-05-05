from client_manager_plugin_app.config import get_settings
import os
from celery import Celery

broker = get_settings().celery_broker_url
celery = Celery("client_manager_uploads", broker=broker)
# Simple config: no result backend required for PoC
celery.conf.update(task_acks_late=True, worker_prefetch_multiplier=1)
