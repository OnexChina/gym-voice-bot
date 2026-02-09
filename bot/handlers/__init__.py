"""Регистрация всех роутеров обработчиков."""

from aiogram import Router

from . import start, workout, programs, stats


def setup_handlers() -> Router:
    router = Router()

    router.include_router(start.router)
    router.include_router(workout.router)
    router.include_router(programs.router)
    router.include_router(stats.router)

    return router
