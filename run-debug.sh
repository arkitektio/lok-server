
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
python manage.py runserver 0.0.0.0:8000