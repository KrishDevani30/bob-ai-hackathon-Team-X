"""SQLAlchemy ORM models for persistent storage."""

from __future__ import annotations

import json
from datetime import datetime

from sqlalchemy import (
    Boolean, Column, DateTime, Float, Integer,
    String, Text, create_engine
)
from sqlalchemy.orm import DeclarativeBase, sessionmaker


class Base(DeclarativeBase):
    pass


class SiteRecord(Base):
    __tablename__ = "sites"

    site_id = Column(String, primary_key=True)
    site_name = Column(String, nullable=False)
    country = Column(String, nullable=False)
    principal_investigator = Column(String)
    activation_date = Column(String)
    staff_count = Column(Integer)
    monitoring_visits_completed = Column(Integer)


class PatientRecord(Base):
    __tablename__ = "patients"

    patient_id = Column(String, primary_key=True)
    site_id = Column(String, nullable=False)
    age = Column(Integer)
    sex = Column(String)
    diagnosis_code = Column(String)
    enrollment_date = Column(String)
    ecog_ps = Column(Integer)
    creatinine_clearance_ml_min = Column(Float)
    alt_x_uln = Column(Float)
    qtcf_ms = Column(Integer)
    prior_cdk46_inhibitor = Column(Boolean)
    active_cns_mets = Column(Boolean)
    pregnant_or_breastfeeding = Column(Boolean)


class VisitRecord(Base):
    __tablename__ = "visits"

    visit_id = Column(String, primary_key=True)
    patient_id = Column(String, nullable=False)
    site_id = Column(String, nullable=False)
    protocol_visit_id = Column(String)
    scheduled_date = Column(String)
    actual_date = Column(String, nullable=True)
    dose_administered_mg = Column(Float, nullable=True)
    assessments_completed = Column(Text)  # JSON array
    data_entry_date = Column(String)

    def set_assessments(self, assessments: list[str]) -> None:
        self.assessments_completed = json.dumps(assessments)

    def get_assessments(self) -> list[str]:
        if self.assessments_completed:
            return json.loads(self.assessments_completed)
        return []


class ConmedRecord(Base):
    __tablename__ = "conmed_records"

    record_id = Column(String, primary_key=True)
    patient_id = Column(String, nullable=False)
    site_id = Column(String, nullable=False)
    drug_name = Column(String)
    atc_class = Column(String)
    start_date = Column(String)
    stop_date = Column(String)
    is_prohibited = Column(Boolean, default=False)


class DeviationRecord(Base):
    __tablename__ = "deviations"

    deviation_id = Column(String, primary_key=True)
    patient_id = Column(String, nullable=False)
    site_id = Column(String, nullable=False)
    visit_id = Column(String, nullable=True)
    rule_id = Column(String)
    deviation_type = Column(String)
    detected_at = Column(DateTime, default=datetime.utcnow)
    magnitude = Column(Float, nullable=True)
    context = Column(Text)  # JSON
    is_safety_critical = Column(Boolean, default=False)
    severity = Column(String, nullable=True)
    severity_source = Column(String, nullable=True)
    rationale = Column(Text, nullable=True)

    def set_context(self, ctx: dict) -> None:
        self.context = json.dumps(ctx, default=str)

    def get_context(self) -> dict:
        if self.context:
            return json.loads(self.context)
        return {}


class SiteRiskRecord(Base):
    __tablename__ = "site_risk"

    site_id = Column(String, primary_key=True)
    score = Column(Float)
    risk_band = Column(String)
    total_deviations = Column(Integer)
    total_visits = Column(Integer)
    indicators_json = Column(Text)  # JSON
    computed_at = Column(DateTime, default=datetime.utcnow)


def get_engine(database_url: str = "sqlite:///./ctrm.db"):
    return create_engine(database_url, connect_args={"check_same_thread": False})


def get_session_factory(engine):
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def create_tables(engine) -> None:
    Base.metadata.create_all(bind=engine)
