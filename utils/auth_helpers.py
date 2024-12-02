from functools import wraps
from flask import session, redirect

def login_required(allowed_roles):
    def decorator(f):
        @wraps(f)
        def decorated_function(*args, **kwargs):
            if 'user_id' not in session:
                return redirect("/login")
            if session.get('role') not in allowed_roles:
                if session.get('role') == "Manager":
                    return redirect("/view_availability")
                
            return f(*args, **kwargs)
        return decorated_function
    return decorator
