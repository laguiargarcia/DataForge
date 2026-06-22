from .tasks import register
from .tasks.error_handler import ErrorHandler
from .tasks.job_handler import JobHandler
from .tasks.message import MessageHandler
from .tasks.python_handler import PythonHandler

for _cls in (PythonHandler, JobHandler, ErrorHandler, MessageHandler):
    try:
        register(_cls)
    except ValueError:
        pass
