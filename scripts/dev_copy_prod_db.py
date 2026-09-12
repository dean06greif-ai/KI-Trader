"""Read-only copy of the production Atlas DB into the local dev Mongo (never writes to prod)."""
import os, sys
from pymongo import MongoClient
from dotenv import load_dotenv

load_dotenv('/app/backend/.env')
src = MongoClient(os.environ['PROD_MONGO_URL'], serverSelectionTimeoutMS=20000)['crypto_scanner']
dst = MongoClient(os.environ['MONGO_URL'])[os.environ['DB_NAME']]
skip_big = {'candle_cache', 'candles', 'indicator_cache'}
for name in src.list_collection_names():
    n = src[name].estimated_document_count()
    if name in skip_big or n > 60000:
        print(f'skip {name} ({n})'); continue
    dst[name].drop()
    batch = []
    for d in src[name].find():
        batch.append(d)
        if len(batch) >= 2000:
            dst[name].insert_many(batch, ordered=False); batch = []
    if batch:
        dst[name].insert_many(batch, ordered=False)
    print(f'{name}: {n}')
print('DONE')
