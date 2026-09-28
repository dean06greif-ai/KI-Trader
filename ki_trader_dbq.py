import pymongo, json
c = pymongo.MongoClient("mongodb+srv://dean06greif1_db_user:KryptoAlert%21@cluster0.zp8xvmz.mongodb.net/?appName=Cluster0", serverSelectionTimeoutMS=20000)
db = c["crypto_scanner"]
names = db.list_collection_names()
print([n for n in names if "regime" in n])
for col in [n for n in names if "regime" in n]:
    print(col, db[col].estimated_document_count())
