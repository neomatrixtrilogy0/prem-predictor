from flask_sqlalchemy import SQLAlchemy
from datetime import datetime

db = SQLAlchemy()

class Player(db.Model):
    __tablename__ = "players"
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(80), unique=True, nullable=False)

class Match(db.Model):
    __tablename__ = "matches"
    id = db.Column(db.Integer, primary_key=True)  # local id
    api_match_id = db.Column(db.Integer, unique=True, nullable=False)  # football-data match id
    competition = db.Column(db.String(10), nullable=False)
    season = db.Column(db.Integer, nullable=True)
    matchday = db.Column(db.Integer, nullable=False)
    utc_date = db.Column(db.DateTime, nullable=False)
    home_team = db.Column(db.String(120), nullable=False)
    away_team = db.Column(db.String(120), nullable=False)
    status = db.Column(db.String(20), nullable=False)  # SCHEDULED, FINISHED, etc.
    home_score = db.Column(db.Integer, nullable=True)
    away_score = db.Column(db.Integer, nullable=True)

class Prediction(db.Model):
    __tablename__ = "predictions"
    id = db.Column(db.Integer, primary_key=True)
    player_id = db.Column(db.Integer, db.ForeignKey('players.id'), nullable=False)
    match_id = db.Column(db.Integer, db.ForeignKey('matches.id'), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)
    pick = db.Column(db.String(10), nullable=False)  # 'HOME','AWAY','DRAW'
    # unique per player & match:
    __table_args__ = (db.UniqueConstraint('player_id','match_id', name='uix_player_match'),)

class ManualResult(db.Model):
    """
    Optional: store manual overrides for GW1-3 results if you want to enter them manually.
    We'll mark Match rows for GW1-3 as FINISHED with the score set manually here or by editing Match.
    """
    __tablename__ = "manual_results"
    id = db.Column(db.Integer, primary_key=True)
    match_api_id = db.Column(db.Integer, nullable=False)
    home_score = db.Column(db.Integer, nullable=False)
    away_score = db.Column(db.Integer, nullable=False)
    entered_at = db.Column(db.DateTime, default=datetime.utcnow)
