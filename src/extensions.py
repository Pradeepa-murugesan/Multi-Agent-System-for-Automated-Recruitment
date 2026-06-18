from flask_limiter import Limiter
from flask_limiter.util import get_remote_address

# Initialised without app — call limiter.init_app(app) in main.py
limiter = Limiter(key_func=get_remote_address, default_limits=[])
