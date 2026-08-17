from handlers.registration import router as registration_router
from handlers.tasks import router as tasks_router
from handlers.photo_report import router as photo_router
from handlers.admin import router as admin_router
from handlers.payment import router as payment_router

routers = [
    registration_router,
    admin_router,
    tasks_router,
    photo_router,
    payment_router,
]
