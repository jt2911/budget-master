import mysql.connector

def get_db():
    conn = mysql.connector.connect(
        host="127.0.0.1",
        port=3306,
        user="root",
        password="",        # XAMPP default = empty
        database="budget_master"
    )
    return conn