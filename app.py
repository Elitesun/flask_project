from flask import Flask, request, render_template, redirect, url_for, send_file, jsonify
import sqlite3
import pandas as pd
from io import BytesIO
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas
from datetime import datetime

app = Flask(__name__)

# Database setup
def init_db():
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    
    # Drop existing tables to update schema
    c.execute('DROP TABLE IF EXISTS transaction_items')
    c.execute('DROP TABLE IF EXISTS transactions')
    c.execute('DROP TABLE IF EXISTS clients')
    
    # Create tables with updated schema
    c.execute('''CREATE TABLE clients
                 (id INTEGER PRIMARY KEY, 
                  name TEXT NOT NULL, 
                  email TEXT NOT NULL)''')
                  
    c.execute('''CREATE TABLE transactions
                 (id INTEGER PRIMARY KEY,
                  client_id INTEGER NOT NULL,
                  total_amount REAL NOT NULL,
                  date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                  FOREIGN KEY(client_id) REFERENCES clients(id))''')
                  
    c.execute('''CREATE TABLE transaction_items
                 (id INTEGER PRIMARY KEY,
                  transaction_id INTEGER NOT NULL,
                  product_name TEXT NOT NULL,
                  quantity INTEGER NOT NULL,
                  price REAL NOT NULL,
                  FOREIGN KEY(transaction_id) REFERENCES transactions(id))''')
    
    conn.commit()
    conn.close()

init_db()

@app.route('/')
def index():
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    # Get all clients with their total transactions
    c.execute("""
        SELECT 
            c.id, 
            c.name, 
            c.email,
            COUNT(DISTINCT t.id) as transaction_count,
            COALESCE(SUM(t.total_amount), 0) as total_amount
        FROM clients c
        LEFT JOIN transactions t ON c.id = t.client_id
        GROUP BY c.id, c.name, c.email
        ORDER BY c.name
    """)
    clients = c.fetchall()
    conn.close()
    return render_template('index.html', clients=clients)

@app.route('/register', methods=['GET', 'POST'])
def register():
    message = None
    if request.method == 'POST':
        name = request.form['name']
        email = request.form['email']
        if name and email:
            conn = sqlite3.connect('database.db')
            c = conn.cursor()
            c.execute("INSERT INTO clients (name, email) VALUES (?, ?)", (name, email))
            conn.commit()
            conn.close()
            return redirect(url_for('index'))
        else:
            message = "Veuillez remplir tous les champs"
    return render_template('register.html', message=message)

@app.route('/payment', methods=['GET', 'POST'])
def payment():
    message = None
    if request.method == 'POST':
        try:
            # Check if the request is JSON
            if request.is_json:
                data = request.get_json()
                client_id = data.get('client_id')
                items = data.get('items', [])
            else:
                # Handle form data
                client_id = request.form.get('client_id')
                # Parse items from form data
                items = []
                product_names = request.form.getlist('product_name')
                quantities = request.form.getlist('quantity')
                prices = request.form.getlist('price')
                
                for i in range(len(product_names)):
                    if product_names[i] and quantities[i] and prices[i]:
                        items.append({
                            'product_name': product_names[i],
                            'quantity': int(quantities[i]),
                            'price': float(prices[i])
                        })

            if client_id and items:
                conn = sqlite3.connect('database.db')
                c = conn.cursor()
                
                try:
                    # Calculate total amount
                    total_amount = sum(float(item['price']) * int(item['quantity']) for item in items)
                    
                    # Create transaction
                    c.execute("""
                        INSERT INTO transactions (client_id, total_amount, date) 
                        VALUES (?, ?, CURRENT_TIMESTAMP)
                    """, (client_id, total_amount))
                    
                    transaction_id = c.lastrowid
                    
                    # Add transaction items
                    for item in items:
                        c.execute("""
                            INSERT INTO transaction_items 
                            (transaction_id, product_name, quantity, price)
                            VALUES (?, ?, ?, ?)
                        """, (transaction_id, 
                             item['product_name'], 
                             int(item['quantity']), 
                             float(item['price'])))
                    
                    conn.commit()
                    
                    if request.is_json:
                        return jsonify({'success': True, 'redirect': url_for('receipt', client_id=client_id)})
                    return redirect(url_for('receipt', client_id=client_id))
                
                except Exception as e:
                    conn.rollback()
                    message = f"Erreur lors de l'enregistrement: {str(e)}"
                finally:
                    conn.close()
            else:
                message = "Veuillez remplir tous les champs requis"
        except Exception as e:
            message = f"Erreur: {str(e)}"
            
        if request.is_json:
            return jsonify({'error': message}), 400
        
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute("SELECT * FROM clients ORDER BY name")
    clients = c.fetchall()
    conn.close()
    return render_template('payment.html', clients=clients, message=message)

@app.route('/receipt/<int:client_id>')
def receipt(client_id):
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute("SELECT * FROM clients WHERE id=?", (client_id,))
    client = c.fetchone()
    
    if client:
        # Get all transactions for the client
        c.execute("""
            SELECT t.id, t.total_amount, t.date,
                   ti.product_name, ti.quantity, ti.price
            FROM transactions t
            JOIN transaction_items ti ON t.id = ti.transaction_id
            WHERE t.client_id=?
            ORDER BY t.date DESC, t.id DESC
        """, (client_id,))
        transactions = c.fetchall()
        
        # Group items by transaction
        grouped_transactions = {}
        total_amount = 0
        for t in transactions:
            trans_id = t[0]
            if trans_id not in grouped_transactions:
                grouped_transactions[trans_id] = {
                    'date': t[2],
                    'total': t[1],
                    'items': []
                }
                total_amount += t[1]
            
            grouped_transactions[trans_id]['items'].append({
                'product_name': t[3],
                'quantity': t[4],
                'price': t[5],
                'subtotal': t[4] * t[5]
            })
        
        conn.close()
        return render_template('receipt.html', 
                             client=client,
                             transactions=grouped_transactions,
                             total_amount=total_amount)
    else:
        conn.close()
        return redirect(url_for('index'))

@app.route('/download/<int:client_id>/<string:file_type>')
def download(client_id, file_type):
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute("SELECT * FROM clients WHERE id=?", (client_id,))
    client = c.fetchone()
    
    # Get all transactions and items
    c.execute("""
        SELECT t.id, t.date, t.total_amount,
               ti.product_name, ti.quantity, ti.price
        FROM transactions t
        JOIN transaction_items ti ON t.id = ti.transaction_id
        WHERE t.client_id=?
        ORDER BY t.date DESC, t.id DESC
    """, (client_id,))
    transactions = c.fetchall()
    
    total_amount = sum(t[2] for t in transactions)
    conn.close()

    if file_type == 'excel':
        output = BytesIO()
        # Create DataFrame with detailed transaction information
        data = []
        for t in transactions:
            data.append({
                'Transaction ID': t[0],
                'Date': t[1],
                'Produit': t[3],
                'Quantité': t[4],
                'Prix Unitaire (CFA)': t[5],
                'Sous-total (CFA)': t[4] * t[5]
            })
        df = pd.DataFrame(data)
        df.to_excel(output, index=False)
        output.seek(0)
        return send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            download_name='recu.xlsx',
            as_attachment=True
        )

    elif file_type == 'pdf':
        output = BytesIO()
        p = canvas.Canvas(output, pagesize=letter)
        
        # Header
        p.setFont("Helvetica-Bold", 16)
        p.drawString(50, 750, f"Reçu pour {client[1]}")
        p.setFont("Helvetica", 12)
        p.drawString(50, 730, f"Email: {client[2]}")
        p.drawString(50, 710, f"Date: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        
        # Group transactions
        current_transaction = None
        y = 670
        
        for t in transactions:
            if current_transaction != t[0]:
                # New transaction header
                if y < 100:  # Start new page if near bottom
                    p.showPage()
                    y = 750
                p.setFont("Helvetica-Bold", 12)
                p.drawString(50, y, f"Transaction du {t[1]}")
                y -= 20
                p.setFont("Helvetica-Bold", 10)
                p.drawString(50, y, "Produit")
                p.drawString(250, y, "Quantité")
                p.drawString(350, y, "Prix (CFA)")
                p.drawString(450, y, "Sous-total (CFA)")
                y -= 15
                current_transaction = t[0]
            
            # Transaction item
            p.setFont("Helvetica", 10)
            subtotal = t[4] * t[5]
            p.drawString(50, y, str(t[3]))
            p.drawString(250, y, str(t[4]))
            p.drawString(350, y, f"{t[5]:,.0f}")
            p.drawString(450, y, f"{subtotal:,.0f}")
            y -= 20
            
            if y < 50:  # Start new page if near bottom
                p.showPage()
                y = 750
        
        # Total
        p.setFont("Helvetica-Bold", 12)
        y -= 20
        p.drawString(300, y, f"Montant Total: {total_amount:,.0f} CFA")
        
        p.showPage()
        p.save()
        output.seek(0)
        return send_file(
            output,
            mimetype='application/pdf',
            download_name='recu.pdf',
            as_attachment=True
        )

if __name__ == '__main__':
    app.run(debug=True)
