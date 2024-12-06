from functools import wraps
import pandas as pd
from flask import Flask, render_template, jsonify, request, redirect, url_for, session
from flask import send_from_directory
from sqlalchemy import create_engine, text
from distutils.command.build_scripts import first_line_re 
import urllib
import logging
from datetime import datetime
import pyodbc
import os
from flask_bcrypt import Bcrypt
from utils.combined_workbook_creation import create_combined_workbook
from utils.auth_helpers import login_required
from flask.cli import with_appcontext
import click
from sqlalchemy.sql import bindparam


app = Flask(__name__, template_folder="/Users/mac/Downloads/Work Availability UI")
bcrypt = Bcrypt(app)
logging.basicConfig(level=logging.DEBUG)

driver = 'ODBC Driver 17 for SQL Server'
server = '127.0.0.1,1433'
database = 'templdb'
username = 'sa'  
password = 'VeryStr0ngP@ssw0rd'

params = urllib.parse.quote_plus(
    f'driver={{{driver}}};'
    f'server={server};'
    f'database={database};'
    f'uid={username};'
    f'pwd={password};'
    'encrypt=no;'
    'trustservercertificate=yes;'
    'connection timeout=30;'
)


db_uri = f"mssql+pyodbc:///?odbc_connect={params}"
engine = create_engine(db_uri)

def save_user_info(first_name, last_name, username, password, role):
    """
    Saves user information into the database with an encrypted password.
    """
    print("Creating user...")
    encrypted_password = bcrypt.generate_password_hash(password).decode('utf-8')

    query = """
    INSERT INTO Users (first_name, last_name, username, password, role)
    VALUES (:first_name, :last_name, :username, :password, :role)
    """
    
    try:
        with engine.connect() as conn:
            conn.execute(
                text(query),
                {
                    "first_name": first_name,
                    "last_name": last_name,
                    "username": username,
                    "password": encrypted_password,
                    "role": role
                }
            )
            conn.commit()
        return {"message": f"User information saved successfully username: {username}, password: {password}"}
    except Exception as e:
        return {"error": str(e)}

def get_user_id_by_username(username):
    query = f"SELECT user_id FROM users WHERE username = '{username}'"
    with engine.connect() as conn:
            result = conn.execute(
                text(query)
            )
            user = result.fetchone()
    return user[0] if len(user) > 0 else None


def save_manager_contract(user_id):
    query = f"INSERT INTO manager_contract (user_id) VALUES ({user_id})"
    with engine.connect() as conn:
        conn.execute(
            text(query)
        )
        conn.commit()


@app.route('/')
@login_required(["Administrator", "finance_team"])
def index():
    query_contract_data = """
    SELECT DISTINCT c.contract_id, c.PrimeContractNumber, c.TaskNumber, c.Name
    FROM Contract c
    INNER JOIN dbo.periodofperformance p ON c.contract_id = p.contract_id
    """
    
    with engine.connect() as conn:
        contract_data_df = pd.read_sql_query(query_contract_data, conn)
        contract_data = contract_data_df.to_dict(orient='records')
        print(contract_data)
    
    return render_template('index.html', contracts=contract_data)



@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == "POST":
        username = request.form.get('username')
        password = request.form.get('password')

        query = "SELECT * FROM Users WHERE username = ?"
        with engine.connect() as conn:
            user = pd.read_sql_query(query, conn, params=(username,)) 

        if not user.empty:
            stored_password = user.iloc[0]['password']
            if bcrypt.check_password_hash(stored_password, password):
                session['user_id'] = int(user.iloc[0]['user_id'])  
                session['role'] = str(user.iloc[0]['role'])  
                return redirect("/")

        return render_template('login.html', message="Invalid Username or password")
    else:
        if 'user_id' in session:
            return redirect("/")
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear() 
    return redirect(url_for('login')) 

@app.route('/get_period_of_performance/<contract_id>')
@login_required(['Manager', "Administrator"])
def get_period_of_performance(contract_id):
    query = "select pop_id from dbo.periodofperformance where contract_id = ?"
    with engine.connect() as conn:
        pops = pd.read_sql_query(query, conn, params=(contract_id,))
    pop_list = [{'pop_id': int(row['pop_id'])} for _, row in pops.iterrows()]
    return jsonify(pop_list)

@app.route('/get_months/<pop_id>')
@login_required(['Manager', "Administrator"])
def get_months(pop_id):
    query = "select startdate, enddate from dbo.periodofperformance where pop_id = ?"
    with engine.connect() as conn:
        result = pd.read_sql_query(query, conn, params=(pop_id,))
    
    if result.empty:
        return jsonify([])

    start_date = result.loc[0, 'startdate']
    end_date = result.loc[0, 'enddate']
    months = []
    current_date = start_date
    while current_date <= end_date:
        month_name = current_date.strftime('%B %Y')
        months.append({'month': month_name, 'date': current_date.strftime('%Y-%m-01')})
        if current_date.month == 12:
            current_date = current_date.replace(year=current_date.year + 1, month=1)
        else:
            current_date = current_date.replace(month=current_date.month + 1)

    return jsonify(months)

@app.route('/get_employees')
@login_required(["Administrator"])
def get_employees():
    query = """
        select distinct e.employee_id,
            case when e.istbd = 1 then e.firstname else e.firstname + ', ' + e.lastname end as employeename
        from employee e
    """
    with engine.connect() as conn:
        employees = pd.read_sql_query(query, conn)
    employee_list = employees.to_dict(orient='records')
    return jsonify(employee_list)

@app.route('/get_hours/<contract_id>/<pop_id>/<month>/<employee_id>')
@login_required(['Manager', "Administrator"])
def get_hours(contract_id, pop_id, month, employee_id):
    query = """
        select availablehours
        from workavailabilityoverride wao
        join periodofperformance pop on wao.employee_id = ? and wao.dateavailable = ? 
        where pop.contract_id = ? and pop.pop_id = ?
    """
    with engine.connect() as conn:
        result = pd.read_sql_query(query, conn, params=(employee_id, month, contract_id, pop_id))
    
    availability_data = [{'availablehours': int(row['availablehours'])} for row in result.to_dict(orient='records')]
    return jsonify(availability_data)

@app.route('/get_jobs')
@login_required(["Administrator"])
def get_jobs():
    query = "select job_id, Title from job"
    with engine.connect() as conn:
        jobs = pd.read_sql_query(query, conn)
    return jsonify(jobs.to_dict(orient='records'))

@app.route('/get_job_for_update/<employee_id>/<month>')
@login_required(["Administrator"])
def get_job_for_update(employee_id, month):
    query = """
        select job_id from workavailabilityoverride
        where employee_id = ? and dateavailable = ?
    """
    with engine.connect() as conn:
        jobs = pd.read_sql_query(query, conn, params=(employee_id, month))
    
    if jobs.empty:
        query = "select job_id, Title from job"
        jobs = pd.read_sql_query(query, conn)
    
    return jsonify(jobs.to_dict(orient='records'))

@app.route('/get_labor_categories')
@login_required(["Administrator"])
def get_labor_categories():
    query = "select laborcategory_id, Name from laborcategory"
    with engine.connect() as conn:
        labor_categories = pd.read_sql_query(query, conn)
    return jsonify(labor_categories.to_dict(orient='records'))

@app.route('/get_labor_category_for_update/<employee_id>/<month>')
@login_required(["Administrator"])
def get_labor_category_for_update(employee_id, month):
    query = """
        select laborcategory_id from workavailabilityoverride
        where employee_id = ? and dateavailable = ?
    """
    with engine.connect() as conn:
        labor_categories = pd.read_sql_query(query, conn, params=(employee_id, month))
    
    if labor_categories.empty:
        query = "select laborcategory_id from laborcategory"
        labor_categories = pd.read_sql_query(query, conn)
    
    return jsonify(labor_categories.to_dict(orient='records'))

@app.route('/update_availability', methods=['POST'])
@login_required(['Manager', "Administrator"])
def update_availability():
    action = request.form.get('action')
    contract_id = request.form.get('contract')
    pop_id = request.form.get('period_of_performance')
    month = request.form.get('month')
    employee_id = request.form.get('employee')
    laborcategory_id = request.form.get('labor_category', 0)
    job_id = request.form.get('job')  
    available_hours = request.form.get('adjusted_hours', 0)
    work_hours_percentage = request.form.get('planned_time_off', 0.0)

    if action in ['save', 'update'] and not (contract_id and pop_id and month and employee_id):
        return redirect(url_for('index', message="All fields except Job ID are required."))

    try:
        with engine.connect() as conn:
            trans = conn.begin()
            try:
                if action == 'save':
                    # Insert a new record
                    stmt = text("""
                        INSERT INTO workavailabilityoverride 
                        (employee_id, laborcategory_id, job_id, dateavailable, availablehours, workhourspercentage)
                        VALUES (:employee_id, :laborcategory_id, :job_id, :dateavailable, :available_hours, :work_hours_percentage)
                    """)
                    conn.execute(stmt, {
                        'employee_id': employee_id,
                        'laborcategory_id': laborcategory_id if laborcategory_id else None,
                        'job_id': job_id if job_id else None,
                        'dateavailable': month,
                        'available_hours': available_hours,
                        'work_hours_percentage': work_hours_percentage
                    })
                    trans.commit()
                    message = "New availability record saved successfully."

                elif action == 'remove_override':
                    # Remove the override by deleting the record
                    delete_stmt = text("""
                        DELETE FROM workavailabilityoverride
                        WHERE employee_id = :employee_id AND dateavailable = :dateavailable
                    """)
                    conn.execute(delete_stmt, {
                        'employee_id': employee_id,
                        'dateavailable': month
                    })
                    trans.commit()
                    message = "Override removed successfully."

                else:
                    message = "No valid action specified."

            except Exception as e:
                trans.rollback()
                message = "An error occurred while processing your request."

    except Exception as e:
        logging.error(f"Database error: {e}")
        message = "A database error occurred."

    return redirect(url_for('index', message=message))


@app.route('/view_availability')
@login_required(['Manager', "Administrator", "finance_team"])
def view_availability():
    try:
        user_role = session.get('role')
        user_id = session.get('user_id')
        
        base_query = """
            SELECT wa.*, 
                e.FirstName, 
                e.LastName, 
                lc.Name AS LaborCategoryName,
                jb.Title AS JobName,
                pop.contract_id AS PeriodOfPerformance,
                pop.StartDate AS StartDate,
                pop.EndDate AS EndDate,
                c.Name AS ContractName
            FROM WorkAvailability wa
            JOIN Employee e ON wa.employee_id = e.employee_id
            JOIN LaborCategory lc ON wa.laborcategory_id = lc.laborcategory_id
            JOIN Job jb ON wa.job_id = jb.job_id
            JOIN PeriodOfPerformance pop ON wa.pop_id = pop.pop_id
            JOIN Contract c ON pop.contract_id = c.contract_id
        """
        
        with engine.connect() as conn:
            if user_role.lower() == "manager":
                contract_query = """
                    SELECT contract_id 
                    FROM manager_contract 
                    WHERE user_id = :user_id AND contract_id IS NOT NULL
                """
                contract_ids = conn.execute(text(contract_query), {"user_id": user_id}).fetchall()
                contract_ids = [row[0] for row in contract_ids]
                
                if contract_ids:
                    base_query += " WHERE c.contract_id IN :contract_ids"
                    result = conn.execute(text(base_query).bindparams(bindparam("contract_ids", expanding=True)),
                {"contract_ids": contract_ids})
                else:
                    result = []
            else:
                result = conn.execute(text(base_query))

            availability_data = result.fetchall() if result else []
    except Exception as e:
        logging.error(f"Error fetching availability data: {e}")
        availability_data = []

    return render_template('view_availability.html', data=availability_data)


@app.route('/view_availability_override')
@login_required(['Manager', 'Administrator', 'finance_team'])
def view_work_availability_by_contract():
    user_id = session.get('user_id')
    user_role = session.get('role')
    try:
        with engine.connect() as conn:
            if user_role.lower() == "manager":
                contract_query = """
                    SELECT contract_id 
                    FROM manager_contract 
                    WHERE user_id = :user_id AND contract_id IS NOT NULL
                """
                contract_ids = conn.execute(text(contract_query), {"user_id": user_id}).fetchall()
                contract_ids = [row[0] for row in contract_ids]
                contract_ids_str = ', '.join(map(str, contract_ids))
                print(contract_ids_str)
                employee_query = f"""
                        SELECT DISTINCT 
                            e.employee_id
                        FROM 
                            Contract c
                        JOIN 
                            PeriodOfPerformance pop ON c.contract_id = pop.contract_id
                        LEFT JOIN 
                            WorkAvailability wa ON wa.pop_id = pop.pop_id
                        LEFT JOIN 
                            Employee e ON wa.employee_id = e.employee_id
                        WHERE 
                            pop.contract_id IN ({contract_ids_str})
                        ORDER BY 
                            e.employee_id;
                    """
                result = conn.execute(text(employee_query))
                employee_ids = [row.employee_id for row in result.fetchall()]
                employee_ids_str = ', '.join(map(str, employee_ids))
                employee_ids_str = f"({employee_ids_str})"
                print(employee_ids_str)
                if not employee_ids:
                    return render_template('view_workavailability_override.html', data=[])

                availability_query = f"""
                    SELECT wa.*, 
                        e.FirstName, 
                        e.LastName, 
                        lc.Name AS LaborCategoryName,
                        jb.Title AS JobName
                    FROM workavailabilityoverride wa
                    LEFT JOIN Employee e ON wa.employee_id = e.employee_id
                    LEFT JOIN LaborCategory lc ON wa.laborcategory_id = lc.laborcategory_id
                    LEFT JOIN Job jb ON wa.job_id = jb.job_id
                    WHERE wa.employee_id IN {employee_ids_str};
                """
                availability_result = conn.execute(text(availability_query))
                availability_data = availability_result.fetchall()
            else:
                query = """
                    SELECT wa.*, 
                        e.FirstName, 
                        e.LastName, 
                        lc.Name AS LaborCategoryName,
                        jb.Title AS JobName
                    FROM workavailabilityoverride wa
                    left JOIN Employee e ON wa.employee_id = e.employee_id
                    left JOIN LaborCategory lc ON wa.laborcategory_id = lc.laborcategory_id
                    left JOIN Job jb ON wa.job_id = jb.job_id;
                """
                result = conn.execute(text(query))
                availability_data = result.fetchall()
    except Exception as e:
        logging.error(f"Error fetching work availability override data: {e}")
        availability_data = []

    return render_template('view_workavailabilty_override.html', data=availability_data)


@app.route('/get_contracts', methods=['GET'])
def get_contracts():
    with engine.connect() as conn:
        query = """
            SELECT contract_id, Name FROM Contract 
        """
        result = conn.execute(text(query))
        contracts_data = result.fetchall()


    if result.rowcount == 0:
        return jsonify({"status": "error", "message": "No contracts found"}), 404

    
    contracts_list = [
        {   "contract_id": row[0],
            "contract_name": row[1],
        }
        for row in contracts_data
    ]

    return jsonify({"status": "success", "data": contracts_list}), 200

@app.route('/add_work_availability', methods=['POST', "GET"])
@login_required(["Administrator", "finance_team"])
def add_work_availability():
    if request.method == "GET":
        return render_template("add_work_availability.html")

    employee_id = request.form.get('employee_id')
    laborcategory_id = request.form.get('laborcategory_id')
    job_id = request.form.get('job_id')
    pop_id = request.form.get('pop_id')
    available_hours = request.form.get('available_hours', 1880)  
    work_hours_percentage = request.form.get('work_hours_percentage', 1.0)  

    if not all([employee_id, laborcategory_id, job_id, pop_id]):
        return jsonify({"status": "error", "message": "All fields are required"}), 400

    query = """
        INSERT INTO WorkAvailability (
            employee_id, laborcategory_id, job_id, pop_id, AvailableHours, WorkHoursPercentage
        ) VALUES (
            :employee_id, :laborcategory_id, :job_id, :pop_id, :available_hours, :work_hours_percentage
        )
    """
    
    try:
        with engine.connect() as connection:
            connection.execute(text(query), {
                'employee_id': employee_id,
                'laborcategory_id': laborcategory_id,
                'job_id': job_id,
                'pop_id': pop_id,
                'available_hours': available_hours,
                'work_hours_percentage': work_hours_percentage
            })
            connection.commit()
    except Exception as e:
        return jsonify({"status": "error", "message": f"Failed to add record: {str(e)}"}), 500

    return redirect("/view_availability")

@app.route('/update_work_availability/<int:record_id>', methods=['PUT'])
@login_required(['Manager', "Administrator", "finance_team"])
def update_work_availability(record_id):
    data = request.json

    available_date = int(data.get('available_hours'))
    work_percentage = data.get('work_hours_percentage')

    if not all([available_date, work_percentage]):
        return jsonify({"status": "error", "message": "All fields are required"}), 400

    query = """
        UPDATE WorkAvailability
        SET AvailableHours = :available_date,
            WorkHoursPercentage = :work_percentage
        WHERE workavailability_id = :record_id
    """
    
    with engine.connect() as connection:
        result = connection.execute(text(query), {
            'available_date': available_date,
            'work_percentage': work_percentage,
            'record_id': record_id
        })


        print(f"Rows affected: {result.rowcount}")
        connection.commit()
        if result.rowcount == 0:
            return jsonify({"status": "error", "message": "Record not found"}), 404

    return jsonify({"status": "success", "message": "Record updated successfully"})

@app.route('/update_work_availability_override/<int:record_id>', methods=['PUT'])
@login_required(['Manager', "Administrator", "finance_team"])
def update_work_availability_override(record_id):
    data = request.json

    try:
        available_hours = int(data.get('available_hours'))
        work_percentage = float(data.get('work_hours_percentage'))
        
        if available_hours is None or work_percentage is None:
            return jsonify({"status": "error", "message": "All fields are required"}), 400

        query = """
            UPDATE workavailabilityoverride
            SET availablehours = :available_hours,
                workhourspercentage = :work_percentage
            WHERE workavailabilityoverride_id = :record_id
        """

        with engine.connect() as connection:
            result = connection.execute(text(query), {
                'available_hours': available_hours,
                'work_percentage': work_percentage, 
                'record_id': record_id
            })

            print(f"Rows affected: {result.rowcount}")
            connection.commit()
            if result.rowcount == 0:
                return jsonify({"status": "error", "message": "Record not found"}), 404

    except ValueError as e:
        return jsonify({"status": "error", "message": "Invalid data type provided"}), 400

    return jsonify({"status": "success", "message": "Record updated successfully"})


@app.route('/employees')
@login_required(["Administrator", "finance_team"])
def view_employee():
    try:
        with engine.connect() as conn:
            query = """
                SELECT e.IdFromJamis, e.employee_id, e.FirstName, e.LastName, e.Email, e.IsTbd, 
                    c.Name AS CompanyName, e.NoteForTbd, es.StartDate, es.EndDate, es.DirectRate 
                FROM Employee e
                LEFT JOIN Company c ON e.company_id = c.company_id
                LEFT JOIN EmployeeSalary es ON e.employee_id = es.employee_id
                WHERE e.is_deleted = 0
            """
            result = conn.execute(text(query))
            employees_data = result.fetchall()
            
        return render_template('view_employees.html', data=employees_data)
    except Exception as e:
        logging.error(f"Error fetching employee data: {e}")
        

@app.route('/add_employee', methods=['POST'])
@login_required(["Administrator", "finance_team"])
def add_employee():
    IdFromJamis = request.form.get('IdFromJamis')
    employee_data = {
        "IdFromJamis": IdFromJamis if IdFromJamis else None,
        "FirstName": request.form['FirstName'],
        "LastName": request.form['LastName'],
        "Email": request.form['Email'],
        "IsTbd": bool(request.form.get('IsTbd', False)),
        "company_id": request.form['company_id'],
        "NoteForTbd": request.form.get('NoteForTbd', None)
    }

    salary_data = {
        "StartDate": request.form['StartDate'],
        "EndDate": None,
        "DirectRate": request.form['DirectRate']
    }

    with engine.begin() as conn:
        insert_query = """
            INSERT INTO Employee (IdFromJamis, FirstName, LastName, Email, IsTbd, company_id, NoteForTbd)
            OUTPUT INSERTED.employee_id
            VALUES (:IdFromJamis, :FirstName, :LastName, :Email, :IsTbd, :company_id, :NoteForTbd)
        """
        result = conn.execute(text(insert_query), employee_data)
        employee_id = result.scalar()
        
        if not employee_id:
            return "Error retrieving employee ID", 500

        salary_query = """
            INSERT INTO EmployeeSalary (employee_id, StartDate, EndDate, DirectRate)
            VALUES (:employee_id, :StartDate, :EndDate, :DirectRate)
        """
        salary_data['employee_id'] = employee_id
        conn.execute(text(salary_query), salary_data)

    return redirect('/employees')

@app.route('/add_user', methods=['POST', 'GET'])
@login_required(["Administrator"])
def add_user():
    if request.method == "POST":
        user_data = {
            "first_name": request.form['first_name'],
            "last_name": request.form['last_name'],
            "username": request.form['username'],
            "password": request.form['password'],  
            "role": request.form['role']
        }
        try: 
            response = save_user_info(
                first_name=user_data['first_name'],
                last_name=user_data['last_name'],
                username=user_data['username'],
                password=user_data['password'],
                role=user_data['role'],
            )
            if user_data['role'].lower() == "manager":
                user_id = get_user_id_by_username(user_data['username'])
                if user_id:
                    save_manager_contract(user_id=user_id)
            return render_template('create_user.html', message=response)
        except Exception as e:
            return render_template('create_user.html', error=f"Error occured in adding user: {e}")
    return render_template('create_user.html')

@app.route('/add_employee_form', methods=['GET'])
@login_required(["Administrator", "finance_team"])
def add_employee_form():
    with engine.connect() as conn:
        query = """
            SELECT company_id, Name FROM Company 
        """
        result = conn.execute(text(query))
        companies_data = result.fetchall()
        
    companies_list = [
        {   "company_id": row[0],
            "company_name": row[1],
        }
        for row in companies_data
    ]
    return render_template('add_employees.html', data=companies_list)


@app.route('/generate_file', methods=['POST'])
@login_required(["Administrator"])
def generate_file():
    data = request.json
   
    contract_id = int(data.get("contract", None))
    pop_id = data.get("pop_id", None)
    work_year = data.get("work_year", 2024)
    dc_start_year = int(data.get("dc_start_year", 2023))
    dc_end_year = int(data.get("dc_end_year", 2027))
    file_name = f"Contract_{contract_id}_Combined_spreadsheet.xlsx"
    last_month = data.get("last_month", "08/2024")
    
    print(f"Passing these parameters to script to generate spreadsheet Contract ID: {contract_id}, Work Year: {work_year}, DC start year: {dc_start_year}, DC end year: {dc_end_year}, File Name: {file_name}, Last Month: {last_month}")
    try:
        file_name = create_combined_workbook(
            contract_id=contract_id, 
            last_month_str=last_month,
            work_year=work_year,
            filename=file_name,
            dc_start_year=dc_start_year,
            dc_end_year=dc_end_year
        )

        return send_from_directory("", file_name, as_attachment=True)
    except Exception as e:
        return jsonify({"status": "error", "message": f"An Error occured {e}"}), 500
    
@app.route("/list_users", methods=["GET"])
@login_required(["Administrator"])
def list_users():
    with engine.connect() as conn:
        query = """
            SELECT user_id, first_name, last_name, username, role FROM Users
        """
        result = conn.execute(text(query))
        users = result.fetchall()
    return render_template("list_users.html", users=users)


@app.route("/delete_user/<int:user_id>", methods=["DELETE"])
@login_required(["Administrator"])
def delete_user(user_id):
    with engine.connect() as conn:
        trans = conn.begin()
        try:
            check_query = text("SELECT COUNT(*) FROM manager_contract WHERE user_id = :id")
            result = conn.execute(check_query, {"id": user_id}).scalar()
            if result > 0:
                delete_manager_query = text("DELETE FROM manager_contract WHERE user_id = :id")
                conn.execute(delete_manager_query, {"id": user_id})
            delete_user_query = text("DELETE FROM users WHERE user_id = :id")
            conn.execute(delete_user_query, {"id": user_id})
            trans.commit()
            return jsonify({"status": "success", "message": "User and associated contracts deleted successfully"})
        except Exception as e:
            trans.rollback()
            return jsonify({"status": "error", "message": str(e)}), 500

        
@app.route("/update_user/<int:user_id>", methods=["PUT"])
@login_required(["Administrator"])
def update_user(user_id):
    data = request.json
    new_role = data.get("role")
    if not new_role:
        return jsonify({"status": "error", "message": "Role is required"}), 400
    
    with engine.connect() as conn:
        query = text("UPDATE users SET role = :role WHERE user_id = :id")
        conn.execute(query, {"role": new_role, "id": user_id})
        conn.commit()
    return jsonify({"status": "success", "message": "User role updated successfully"})


@app.route('/employees/update', methods=['POST'])
@login_required(["Administrator", "finance_team"])
def update_employee():
    try:
        data = request.json
        employee_id = int(data['id'])
        start_date = data.get('start_date')
        end_date = data.get('end_date')
        direct_rate = float(data.get('direct_rate'))
        
        with engine.connect() as conn:
            query = """
            UPDATE EmployeeSalary
            SET StartDate = :start_date, EndDate = :end_date, DirectRate = :direct_rate
            WHERE employee_id = :employee_id
            """
            conn.execute(text(query), {
                'start_date': start_date,
                'end_date': end_date,
                'direct_rate': direct_rate,
                'employee_id': employee_id,
            })
            conn.commit()

        return jsonify({'message': 'Employee updated successfully'}), 200
    except Exception as e:
        logging.error(f"Error updating employee: {e}")
        return jsonify({'message': 'Failed to update employee'}), 500


@app.route('/employees/delete/<int:employee_id>', methods=['DELETE'])
@login_required(["Administrator", "finance_team"])
def delete_employee(employee_id):
    try:
        query = "UPDATE Employee SET is_deleted = 1 WHERE employee_id = :employee_id"
        with engine.connect() as conn:
            conn.execute(text(query), {'employee_id': employee_id})
            conn.commit()
        return jsonify({'message': 'Employee deleted successfully'}), 200
    except Exception as e:
        logging.error(f"Error deleting employee: {e}")
        return jsonify({'message': 'Failed to delete employee'}), 500
    
@click.command('create-user')
@with_appcontext
def create_user():
    """
    Command to create a new user via the command line.
    """
    result = save_user_info("admin", "admin", "admin", "admin", "Administrator")
    
    if "error" in result:
        click.echo(f"Error: {result['error']}")
    else:
        click.echo(result["message"])
        
app.cli.add_command(create_user)


if __name__ == '__main__':
    app.run(debug=True)