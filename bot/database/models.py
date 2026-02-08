"""SQLAlchemy 2.0 модели (async, декларативный стиль)."""

from datetime import date, datetime
from decimal import Decimal
from typing import Any, List, Optional

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    """Базовый класс для всех моделей SQLAlchemy."""
    pass


class User(Base):
    __tablename__ = "users"

    telegram_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    username: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    settings: Mapped[Optional[dict[str, Any]]] = mapped_column(JSONB, nullable=True)

    programs: Mapped[list["Program"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    workouts: Mapped[list["Workout"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    records: Mapped[list["Record"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    custom_exercises: Mapped[list["Exercise"]] = relationship(
        "Exercise",
        back_populates="creator",
        foreign_keys="Exercise.created_by",
        cascade="all, delete-orphan",
    )


class Exercise(Base):
    __tablename__ = "exercises"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, unique=True, nullable=False)
    name_en: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    synonyms: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text), nullable=True)
    muscle_groups: Mapped[Optional[list[str]]] = mapped_column(ARRAY(Text), nullable=True)
    equipment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_custom: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_by: Mapped[Optional[int]] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_id", ondelete="CASCADE"),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    creator: Mapped[Optional["User"]] = relationship(
        "User",
        back_populates="custom_exercises",
        foreign_keys=[created_by],
    )
    workout_exercises: Mapped[list["WorkoutExercise"]] = relationship(
        back_populates="exercise",
        cascade="all, delete-orphan",
    )
    records: Mapped[list["Record"]] = relationship(
        back_populates="exercise",
        cascade="all, delete-orphan",
    )


class Program(Base):
    __tablename__ = "programs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    name: Mapped[str] = mapped_column(Text, nullable=False)
    exercises: Mapped[Optional[list[dict[str, Any]]]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="programs")
    workouts: Mapped[list["Workout"]] = relationship(
        back_populates="program",
        cascade="all, delete-orphan",
    )


class Workout(Base):
    __tablename__ = "workouts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    date: Mapped[date] = mapped_column(Date, nullable=False)
    program_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("programs.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    total_volume_kg: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    user: Mapped["User"] = relationship(back_populates="workouts")
    program: Mapped[Optional["Program"]] = relationship(back_populates="workouts")
    workout_exercises: Mapped[list["WorkoutExercise"]] = relationship(
        back_populates="workout",
        cascade="all, delete-orphan",
        order_by="WorkoutExercise.order_num",
    )
    records: Mapped[list["Record"]] = relationship(
        back_populates="workout",
        cascade="all, delete-orphan",
    )


class WorkoutExercise(Base):
    __tablename__ = "workout_exercises"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workout_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("workouts.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    exercise_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("exercises.id", ondelete="RESTRICT"),
        index=True,
        nullable=False,
    )
    order_num: Mapped[int] = mapped_column(Integer, nullable=False)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    volume_kg: Mapped[Optional[Decimal]] = mapped_column(Numeric(12, 2), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    workout: Mapped["Workout"] = relationship(back_populates="workout_exercises")
    exercise: Mapped["Exercise"] = relationship(back_populates="workout_exercises")
    sets: Mapped[list["Set"]] = relationship(
        back_populates="workout_exercise",
        cascade="all, delete-orphan",
        order_by="Set.set_number",
    )


class Set(Base):
    __tablename__ = "sets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    workout_exercise_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("workout_exercises.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    set_number: Mapped[int] = mapped_column(Integer, nullable=False)
    reps: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    weight_kg: Mapped[Optional[Decimal]] = mapped_column(Numeric(10, 2), nullable=True)
    comment: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_warmup: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    workout_exercise: Mapped["WorkoutExercise"] = relationship(back_populates="sets")


class Record(Base):
    __tablename__ = "records"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("users.telegram_id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    exercise_id: Mapped[int] = mapped_column(
        Integer,
        ForeignKey("exercises.id", ondelete="CASCADE"),
        index=True,
        nullable=False,
    )
    record_type: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    workout_id: Mapped[Optional[int]] = mapped_column(
        Integer,
        ForeignKey("workouts.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    achieved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    user: Mapped["User"] = relationship(back_populates="records")
    exercise: Mapped["Exercise"] = relationship(back_populates="records")
    workout: Mapped[Optional["Workout"]] = relationship(back_populates="records")
