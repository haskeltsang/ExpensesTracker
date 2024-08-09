# ExpensesTracker
ExpensesTracker

# How to use

## Install docker and docker-compose
```
https://www.docker.com/
```
## Copy prod.env to .env and change your own variables

## Go to app.py change the secret key db username password
```
            app.secret_key = os.environ.get('FLASK_SECRET_KEY', 'your_default_secret_key')
            host=os.environ.get('MYSQL_HOST', 'db'),
            user=os.environ.get('MYSQL_USER', 'mysql_user'),
            password=os.environ.get('MYSQL_PASSWORD', 'mysql_user'),
            database=os.environ.get('MYSQL_DATABASE', 'expense')
```
## Run the following command
```
docker compose up -d
```

## Open your browser and go to the following link
```
http://localhost
```

# Welcome!
