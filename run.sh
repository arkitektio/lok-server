
echo "=> Waiting for DB to be online"
python manage.py wait_for_database -s 2

echo "=> Performing database migrations..."
python manage.py migrate

echo "=> Ensuring Superusers..."
python manage.py ensureadmin

echo "=> Ensuring Lok Users..."
python manage.py ensureloks

echo "=> Ensuring App..."
python manage.py ensureapps

echo "=> Collecting Static.."
python manage.py collectstatic --noinput

echo "=> Starting Django with Runserver"
gunicorn --bind=0.0.0.0 --log-level=debug --timeout 1000 --workers 1 --threads 4 herre.wsgi 