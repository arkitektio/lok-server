
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

echo "=> Ensuring Configuration..."
python manage.py ensureconfigs

echo "=> Collecting Static.."
python manage.py collectstatic --noinput

echo "=> Starting Django with Runserver"
daphne -b 0.0.0.0 -p 8000 --websocket_timeout -1 herre.asgi:application 