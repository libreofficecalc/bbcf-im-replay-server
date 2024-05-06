from flask import Flask, request, jsonify

from credentials.config import Config
import hashlib
import os
from datetime import datetime
from flask_sqlalchemy import SQLAlchemy
import sqlalchemy
from models import ReplayOrm
from sqlalchemy.exc import SQLAlchemyError


app = Flask(__name__)
app.config.from_object(Config)
db = SQLAlchemy(app)




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
        bytes_to_hash = (bytes_metadata['p1_toon'] + bytes_metadata['p2_toon'] + bytes_metadata['p1_name'] 
                                + bytes_metadata[ 'p2_name'] + bytes_metadata[ 'p1_steamid64']
                                + bytes_metadata['p2_steamid64'] + bytes_metadata['replay_inputs'])
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
        app.run(host = '0.0.0.0', port = 8500, debug=False) #5000



# @app.route('/download/<path:filename>', methods=['GET'])
# def download_replay(fname):
#         print( 'fname: ')
#         print(fname)
#         return flask.send_from_directory(directory = app.config['UPLOAD_FOLDER'], filename = fname, download_name = fname)
        
