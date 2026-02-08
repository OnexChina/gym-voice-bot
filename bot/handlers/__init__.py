"""Регистрация всех роутеров обработчиков."""

from aiogram import Router

from . import start, workout
# from . import programs, stats  # TODO


def setup_handlers() -> Router:
    router = Router()

    router.include_router(start.router)
    router.include_router(workout.router)
    # router.include_router(programs.router)  # TODO
    # router.include_router(stats.router)  # TODO

    return router
