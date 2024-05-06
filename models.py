from flask_sqlalchemy import SQLAlchemy
from sqlalchemy import DateTime
db = SQLAlchemy()

class ReplayOrm(db.Model):
        __tablename__ = "replay_metadata"

        p1 = db.Column(db.String(255))
        p1_toon = db.Column(db.Integer)
        p2 = db.Column(db.String(255))
        p2_toon = db.Column(db.Integer)
        recorder = db.Column(db.String(255))
        winner = db.Column(db.Integer)
        filename = db.Column(db.String(255), primary_key=True)
        datetime_ = db.Column(DateTime)
        upload_datetime_ = db.Column(DateTime)
        p1_steamid64 = db.Column(db.Integer)
        p2_steamid64 = db.Column(db.Integer)
        recorder_steamid64 = db.Column(db.Integer)
        
