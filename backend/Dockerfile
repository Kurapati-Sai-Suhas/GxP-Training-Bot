FROM python:3.12-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN mkdir -p media

# Collect admin/DRF assets for WhiteNoise. DEBUG=True and a throwaway key keep this build
# step from tripping the production SECRET_KEY guard in settings.py; neither value is
# baked into the image or used at runtime.
RUN SECRET_KEY=build-time-only DEBUG=True python manage.py collectstatic --noinput

EXPOSE 8000

# Production default. --timeout 180 is deliberate, not arbitrary: quiz generation waits
# synchronously on a Celery result for up to 120s (ai_engine/views.py), and gunicorn's
# default 30s worker timeout would kill those requests mid-flight.
# The development compose file overrides this with `manage.py runserver`.
CMD ["gunicorn", "config.wsgi:application", \
     "--bind", "0.0.0.0:8000", \
     "--workers", "3", \
     "--timeout", "180", \
     "--access-logfile", "-", \
     "--error-logfile", "-"]
