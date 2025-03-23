from flask import Flask, request, jsonify

from credentials.config import Config
import hashlib
import os
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
import sqlalchemy
import mariadb
from models import ReplayOrm
from sqlalchemy.exc import SQLAlchemyError
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address


app = Flask(__name__)
app.config.from_object(Config)
db = SQLAlchemy(app)

limiter = Limiter(
    key_func=get_remote_address,
    default_limits=["30 per minute"], 
    storage_uri="memory://"
)
limiter.init_app(app)




def fsave_data(data, fname):
        file_path = os.path.join(app.config['UPLOAD_FOLDER'], fname)
        with open(file_path, 'wb') as f:
                f.write(data)
        print('File uploaded sucessfully as {}'.format(fname))
        return file_path

def get_hashed_filename(data):
        
        bytes_metadata = {}
        bytes_metadata['date1'] = data[0x38: 0x38+ 0x18]
        bytes_metadata['winner'] = data[0x98:0x98+0x4][0]
        bytes_metadata['p1_name'] = data[0xa4:0xa4+0x24]
        bytes_metadata['p2_name'] = data[0x16E: 0x16E + 0x24]
        bytes_metadata['p1_toon'] = data[0x230 : 0x230 + 0x4]
        bytes_metadata['p2_toon'] = data[0x234 : 0x234 + 0x4]
        bytes_metadata['recorder'] = data[0x240 : 0x240 + 0x24]
        bytes_metadata['p1_steamid64'] = data[0x9C: 0x9C + 0x8]
        bytes_metadata['p2_steamid64'] = data[0x166: 0x166 + 0x8]
        bytes_metadata['recorder_steamid64'] = data[0x238: 0x238 + 0x8]
        bytes_metadata['replay_inputs'] = data[0x8d0: 0x8d0 + 0xF730]

        """this will ensure that the hash will only use the parts that are the same for all players
        avoiding two replays of the same match to have different hashes due to differente recorder, timezone, etc"""
        bytes_to_hash = (bytes_metadata['p1_toon'] + bytes_metadata['p2_toon'] 
                                + bytes_metadata[ 'p1_steamid64'] + bytes_metadata['p2_steamid64'] 
                                + bytes_metadata['replay_inputs'])
        md5 = hashlib.md5(bytes_to_hash).hexdigest()[:25]
        md5_filename = md5 + '.dat'
        #print(md5_filename)
        return md5_filename

def parse_replay_metadata(data,fname):
        replay_metadata = {}
        replay_metadata['date1'] = data[0x38: 0x38+ 0x18].decode('utf-8')
        replay_metadata['winner'] = data[0x98:0x98+0x4][0]
        replay_metadata['p1_name'] = data[0xa4:0xa4+0x24].decode('utf-16').split('\x00')[0]
        replay_metadata['p2_name'] = data[0x16E: 0x16E + 0x24].decode('utf-16').split('\x00')[0]
        replay_metadata['p1_toon'] = data[0x230 : 0x230 + 0x4][0]
        replay_metadata['p2_toon'] = data[0x234 : 0x234 + 0x4][0]
        replay_metadata['recorder'] = data[0x240 : 0x240 + 0x24].decode('utf-16').split('\x00')[0]
        replay_metadata['date1'] = datetime.strptime(replay_metadata['date1'], "%a %b %d %H:%M:%S %Y")
        replay_metadata['p1_steamid64'] = int.from_bytes(data[0x9C: 0x9C + 0x8], byteorder='little')
        replay_metadata['p2_steamid64'] = int.from_bytes(data[0x166: 0x166 + 0x8], byteorder='little')
        replay_metadata['recorder_steamid64'] = int.from_bytes(data[0x238: 0x238 + 0x8], byteorder='little')
        replay_metadata['filename'] = fname
                
        print(replay_metadata)
        return replay_metadata

from sqlalchemy import text
def db_insert(replay_metadata):
        with app.app_context():
#                db.reflect()
                #                db.session.execute(text('show tables'))
#                print(db.session.execute(text('SELECT * from replay_metadata')).mappings().all())
 #               print(db.metadata.tables)
                #                print(db.metadata.tables.keys())
  #              print(db.metadata)
#                class ReplayMetadataORM(db.Model):
#                        __table__ = db.metadata.tables['replay_metadata']
                try:
#                        new_row = ReplayOrm(date_='2022-01-01', player_one = 't', filename = 'f2342werwer')
                        new_row = ReplayOrm(
                                datetime_ = replay_metadata['date1'],
                                winner = replay_metadata['winner'],
                                p1 = replay_metadata['p1_name'],
                                p2 = replay_metadata['p2_name'],
                                p1_toon = replay_metadata['p1_toon'],
                                p2_toon = replay_metadata['p2_toon'],
                                recorder = replay_metadata['recorder'],
                                filename = replay_metadata['filename'],
                                p1_steamid64 = replay_metadata['p1_steamid64'],
                                p2_steamid64 = replay_metadata['p2_steamid64'],
                                recorder_steamid64 = replay_metadata['recorder_steamid64'],
                                upload_datetime_ = datetime.now()
                                )
                        db.session.add(new_row)
                        db.session.commit()
                        return True
                except SQLAlchemyError as e :
                        #print(e)
                        db.session.rollback()
                        return False
#                print(db.session.execute(text('SELECT * from replay_metadata')).mappings().all())
        
        return False

@app.route('/upload', methods=['POST'])
def upload_file():
        data = request.data
#        db_insert()
        print(request.headers)
        fname = get_hashed_filename(data)
#        md5 = fsave_data(data)
        replay_metadata = parse_replay_metadata(data,fname)
        #This is like this currently cause the hook currently triggers twice(?) per match per player, the first trigger is invalid, so I need the second one to work
        #db_insert(replay_metadata)
        if db_insert(replay_metadata):
                #prob should move this inside db_insert so that if the write fails rolls back the insert too, idk how it would prevent the check for duplicate primary keys before writing tho 
                fsave_data(data, replay_metadata['filename'] )
        
        return jsonify({'message': 'success'}), 200




def get_db_connection():
    return mariadb.connect(
        host=Config.DB_HOST,
        user=Config.DB_USER,
        password=Config.DB_PASSWORD,
        database= Config.DATABASE
    )







def build_query_conditions():
    filters = []
    values = []
    COLUMNS = ['recorder', 'winner', 'filename',
    'datetime_', 'upload_datetime_',
    'p1_toon' , 'p2_toon',
    'p1_steamid64', 'p2_steamid64',
    'recorder_steamid64']

    
    for column in COLUMNS:
        if column in request.args:
            filters.append(f"{column} = ?")
            values.append(request.args[column])

    #p1 and p2 are interchangeable
    player_x = request.args.get('player_x')
    player_y = request.args.get('player_y')

    if player_x and player_y:
        filters.append("((p1 LIKE ? AND p2 LIKE ?) OR (p1 LIKE ? AND p2 LIKE ?))")
        values.extend([f"%{player_x}%", f"%{player_y}%", f"%{player_y}%", f"%{player_x}%"])
    elif player_x:
        filters.append("(p1 LIKE ? OR p2 LIKE ?)")
        values.extend([f"%{player_x}%", f"%{player_x}%"])
    elif player_y:
        filters.append("(p1 LIKE ? OR p2 LIKE ?)")
        values.extend([f"%{player_y}%", f"%{player_y}%"])

    where_clause = f"WHERE {' AND '.join(filters)}" if filters else ""
    return where_clause, values

def get_pagination_params():
    page = int(request.args.get('page', 1))
    page_size = min(int(request.args.get('page_size', 100)), 100)
    offset = (page - 1) * page_size
    return page, page_size, offset

@app.route('/get_replays', methods=['GET'])
def get_replays():
    conn = get_db_connection()
    cursor = conn.cursor()

    where_clause, query_params = build_query_conditions(request.args)
    page, page_size, offset = get_pagination_params()


    query = f"""
        SELECT * FROM replay_metadata 
        {where_clause} 
        ORDER BY upload_datetime_ DESC 
        LIMIT ? OFFSET ?
    """
    cursor.execute(query, query_params + [page_size, offset])

    
    columns = [col[0] for col in cursor.description]
    results = [dict(zip(columns, row)) for row in cursor.fetchall()]

    cursor.close()
    conn.close()

    return jsonify({
        "page": page,
        "page_size": page_size,
        "results": results
    })




if __name__ == '__main__':
        #app.config['UPLOAD_FOLDER']= './replays'
        if not os.path.exists(app.config['UPLOAD_FOLDER']):
                os.makedirs(app.config['UPLOAD_FOLDER'])
        #app.config.from_object(Config)
#        engine = sqlalchemy.create_engine(app.config['SQLALCHEMY_DATABASE_URI'], echo= True)
#        inspector =         sqlalchemy.inspect(engine)
#        print(inspector.get_table_names())
#        print(engine.table_names())
#        db_insert()
        app.run(host = '0.0.0.0', port = 5000, debug=False) #5000



# @app.route('/download/<path:filename>', methods=['GET'])
# def download_replay(fname):
#         print( 'fname: ')
#         print(fname)
#         return flask.send_from_directory(directory = app.config['UPLOAD_FOLDER'], filename = fname, download_name = fname)
        
