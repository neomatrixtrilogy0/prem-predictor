import os
from flask import Flask, render_template, request, redirect, url_for, flash, jsonify
from models import db, Player, Match, Prediction, ManualResult
from sqlalchemy.exc import IntegrityError
import requests
from datetime import datetime, timezone, timedelta
from dotenv import load_dotenv

load_dotenv()

FOOTBALL_DATA_API_KEY = os.getenv("FOOTBALL_DATA_API_KEY")  # you must set this
DATABASE_URL = os.getenv("DATABASE_URL")  # e.g. postgres://user:pass@localhost:5432/dbname
SECRET_KEY = os.getenv("SECRET_KEY", "dev-secret")

if not FOOTBALL_DATA_API_KEY:
    raise RuntimeError("Set FOOTBALL_DATA_API_KEY in environment")

app = Flask(__name__)
app.config['SQLALCHEMY_DATABASE_URI'] = DATABASE_URL or "postgresql://postgres:postgres@localhost/prem-pred"
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SECRET_KEY'] = SECRET_KEY
db.init_app(app)

# Configure competition: Premier League (use code 'PL' in football-data)
COMPETITION_CODE = "PL"

HEADERS = {
    "X-Auth-Token": FOOTBALL_DATA_API_KEY
}

# Helper functions
def fetch_matches_for_matchday(matchday:int):
    """Fetch matches from Football-Data API for the Premier League matchday."""
    url = f"https://api.football-data.org/v4/competitions/{COMPETITION_CODE}/matches?matchday={matchday}"
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    return resp.json().get('matches', [])

def upsert_matches_from_api(matchday:int):
    matches = fetch_matches_for_matchday(matchday)
    added = 0
    for m in matches:
        api_id = m['id']
        utc_date = datetime.fromisoformat(m['utcDate'].replace("Z","+00:00"))
        home = m['homeTeam']['name']
        away = m['awayTeam']['name']
        status = m.get('status', 'SCHEDULED')
        home_score = None
        away_score = None
        if m.get('score') and m['score'].get('fullTime'):
            ft = m['score']['fullTime']
            home_score = ft.get('home')
            away_score = ft.get('away')

        existing = Match.query.filter_by(api_match_id=api_id).first()
        if existing:
            # update
            existing.utc_date = utc_date
            existing.status = status
            existing.home_team = home
            existing.away_team = away
            existing.home_score = home_score
            existing.away_score = away_score
        else:
            new = Match(
                api_match_id=api_id,
                competition=COMPETITION_CODE,
                season=None,
                matchday=matchday,
                utc_date=utc_date,
                home_team=home,
                away_team=away,
                status=status,
                home_score=home_score,
                away_score=away_score
            )
            db.session.add(new)
            added += 1
    db.session.commit()
    return added

def result_of_match(match:Match):
    """Return 'HOME','AWAY','DRAW' if finished and scores known, else None."""
    if match.home_score is None or match.away_score is None:
        return None
    if match.home_score > match.away_score:
        return 'HOME'
    if match.away_score > match.home_score:
        return 'AWAY'
    return 'DRAW'

def points_for_prediction(pred_pick, match:Match):
    res = result_of_match(match)
    if res is None:
        return 0
    return 1 if pred_pick == res else 0

def ensure_six_players():
    # Create placeholder players if not exist (the user can rename later by editing DB)
    default_names = ["Biniam A","Biniam G","Biniam E","Abel","Siem","Kubrom"]
    for name in default_names:
        if not Player.query.filter_by(name=name).first():
            db.session.add(Player(name=name))
    db.session.commit()

@app.before_first_request
def init():
    db.create_all()
    ensure_six_players()

@app.route("/")
def index():
    # Show dropdown with player names and gameweek options (GW4..GW38)
    players = Player.query.all()
    # default GW start from 4 as requested
    gw_options = list(range(4, 39))  # 4..38 inclusive
    return render_template("index.html", players=players, gw_options=gw_options)

@app.route("/fetch_matchday/<int:matchday>")
def fetch_matchday(matchday):
    try:
        added = upsert_matches_from_api(matchday)
        return jsonify({"status":"ok","added":added})
    except Exception as e:
        return jsonify({"status":"error","message":str(e)}), 500

@app.route("/make_prediction", methods=["GET","POST"])
def make_prediction():
    player_id = request.args.get("player_id", type=int)
    matchday = request.args.get("matchday", type=int)
    if request.method == "GET":
        if not player_id or not matchday:
            flash("Pick a player and gameweek first.", "warning")
            return redirect(url_for("index"))
        # ensure matches loaded for that matchday
        upsert_matches_from_api(matchday)
        matches = Match.query.filter_by(matchday=matchday).order_by(Match.utc_date).all()
        player = Player.query.get(player_id)
        # load existing predictions
        existing = {p.match_id: p for p in Prediction.query.filter_by(player_id=player_id).all()}
        return render_template("make_prediction.html", matches=matches, player=player, existing=existing, now_utc=datetime.now(timezone.utc))
    else:
        # POST: submit picks
        player_id = int(request.form["player_id"])
        matchday = int(request.form["matchday"])
        player = Player.query.get(player_id)
        # For each match in this matchday, read pick if provided
        matches = Match.query.filter_by(matchday=matchday).all()
        messages = []
        for match in matches:
            # check if allowed: now < kickoff - 5 minutes
            kickoff = match.utc_date.replace(tzinfo=timezone.utc)
            cutoff = kickoff - timedelta(minutes=5)
            now = datetime.now(timezone.utc)
            if now >= cutoff:
                # not allowed to change any picks for this match
                continue
            pick_key = f"pick_{match.id}"
            pick = request.form.get(pick_key)
            if not pick:
                # Skip empty
                continue
            pick_val = pick.upper()
            # store or update
            existing = Prediction.query.filter_by(player_id=player_id, match_id=match.id).first()
            if existing:
                existing.pick = pick_val
                existing.created_at = datetime.utcnow()
            else:
                p = Prediction(player_id=player_id, match_id=match.id, pick=pick_val)
                db.session.add(p)
        db.session.commit()
        flash("Predictions saved (for matches that are at least 5 minutes from kickoff).", "success")
        return redirect(url_for("confirm", player_id=player_id, matchday=matchday))

@app.route("/confirm")
def confirm():
    player_id = request.args.get("player_id", type=int)
    matchday = request.args.get("matchday", type=int)
    player = Player.query.get(player_id)
    matches = Match.query.filter_by(matchday=matchday).order_by(Match.utc_date).all()
    preds = {p.match_id: p for p in Prediction.query.filter_by(player_id=player_id).all()}
    return render_template("confirm.html", player=player, matchday=matchday, matches=matches, preds=preds, now_utc=datetime.now(timezone.utc))

@app.route("/weekly_results/<int:matchday>")
def weekly_results(matchday):
    # show table of results for each player for the matchday
    # ensure matches loaded
    upsert_matches_from_api(matchday)
    matches = Match.query.filter_by(matchday=matchday).order_by(Match.utc_date).all()
    players = Player.query.order_by(Player.id).all()
    # Build data: player -> list of (match, pick, points)
    table = []
    for player in players:
        row = {"player": player.name, "per_match": [], "sum":0}
        for match in matches:
            pred = Prediction.query.filter_by(player_id=player.id, match_id=match.id).first()
            pick = pred.pick if pred else None
            pts = points_for_prediction(pred.pick, match) if pred else 0
            row["per_match"].append({
                "match": match,
                "pick": pick,
                "points": pts
            })
            row["sum"] += pts
        table.append(row)
    return render_template("weekly_results.html", matchday=matchday, matches=matches, table=table)

@app.route("/totals")
def totals():
    # compute total points over all 38 matchdays
    # ensure we have matches loaded for the played matchdays (fetching matchdays on demand)
    players = Player.query.order_by(Player.id).all()
    # get all matches that have scores or status finished
    matches = Match.query.filter(Match.matchday >= 1).all()
    # We'll compute by checking predictions and match results
    player_totals = []
    for player in players:
        total = 0
        # get player's predictions
        preds = Prediction.query.filter_by(player_id=player.id).all()
        for p in preds:
            match = Match.query.get(p.match_id)
            total += points_for_prediction(p.pick, match)
        player_totals.append({"player":player.name, "total": total})
    # Sort descending
    player_totals.sort(key=lambda x: x['total'], reverse=True)
    return render_template("totals.html", totals=player_totals)

# Admin route for manually entering results for GW1-3
@app.route("/admin/manual_results", methods=["GET","POST"])
def admin_manual_results():
    if request.method == "GET":
        # list matches for GW1-3 so admin can enter scores
        matches = Match.query.filter(Match.matchday.in_([1,2,3])).order_by(Match.matchday, Match.utc_date).all()
        return render_template("admin_manual_results.html", matches=matches)
    else:
        # Accept posted scores for multiple matches
        for key, value in request.form.items():
            if key.startswith("home_"):
                match_id = int(key.split("_",1)[1])
                home_score = int(value)
                away_score = int(request.form.get(f"away_{match_id}", 0))
                match = Match.query.get(match_id)
                if match:
                    match.home_score = home_score
                    match.away_score = away_score
                    match.status = "FINISHED"
                    db.session.add(match)
        db.session.commit()
        flash("Manual results updated for GW1-3.", "success")
        return redirect(url_for("admin_manual_results"))

if __name__ == "__main__":
    app.run(debug=True)
