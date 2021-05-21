
echo "=> Waiting for DB to be online"
python manage.py wait_for_database -s 2

echo "=> Performing database migrations..."
python manage.py migrate

echo "=> Ensuring App..."
python manage.py ensureapps

echo "=> Collecting Static.."
python manage.py collectstatic --noinput

echo "=> Starting Django with G-Unicorn"
gunicorn herre.wsgi -b 0.0.0.0:8000 --log-level debug --timeout 90 --workers 3

