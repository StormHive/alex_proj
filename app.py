
import pandas as pd
from flask import Flask, render_template, jsonify, request, redirect, url_for
from sqlalchemy import create_engine, text
import urllib
import logging
from datetime import datetime
import pyodbc
import os
from utils.combined_workbook_creation import create_combined_workbook


app = Flask(__name__, template_folder='/Users/mac/Downloads/Notepad++ 2')
logging.basicConfig(level=logging.DEBUG)

driver = 'ODBC Driver 17 for SQL Server'
server = '127.0.0.1,1433'  # Or 'localhost'
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
        
@app.route('/')
def index():
    query = "select distinct contract_id from dbo.periodofperformance"
    with engine.connect() as conn:
        contracts_df = pd.read_sql_query(query, conn)
    contracts = contracts_df['contract_id'].tolist()
    return render_template('index.html', contracts=contracts)

@app.route('/get_period_of_performance/<contract_id>')
def get_period_of_performance(contract_id):
    query = "select pop_id from dbo.periodofperformance where contract_id = ?"
    with engine.connect() as conn:
        pops = pd.read_sql_query(query, conn, params=(contract_id,))
    pop_list = [{'pop_id': int(row['pop_id'])} for _, row in pops.iterrows()]
    return jsonify(pop_list)

@app.route('/get_months/<pop_id>')
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
def get_jobs():
    query = "select job_id, Title from job"
    with engine.connect() as conn:
        jobs = pd.read_sql_query(query, conn)
    return jsonify(jobs.to_dict(orient='records'))

@app.route('/get_job_for_update/<employee_id>/<month>')
def get_job_for_update(employee_id, month):
    query = """
        select job_id from workavailabilityoverride
        where employee_id = ? and dateavailable = ?
    """
    with engine.connect() as conn:
        jobs = pd.read_sql_query(query, conn, params=(employee_id, month))
    
    if jobs.empty:
        query = "select job_id from job"
        jobs = pd.read_sql_query(query, conn)
    
    return jsonify(jobs.to_dict(orient='records'))

@app.route('/get_labor_categories')
def get_labor_categories():
    query = "select laborcategory_id, Name from laborcategory"
    with engine.connect() as conn:
        labor_categories = pd.read_sql_query(query, conn)
    return jsonify(labor_categories.to_dict(orient='records'))

@app.route('/get_labor_category_for_update/<employee_id>/<month>')
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

                elif action == 'update':
                    # Update existing record
                    stmt = text("""
                        UPDATE workavailabilityoverride
                        SET laborcategory_id = :laborcategory_id, job_id = :job_id, availablehours = :available_hours, workhourspercentage = :work_hours_percentage
                        WHERE employee_id = :employee_id AND dateavailable = :dateavailable
                    """)
                    conn.execute(stmt, {
                        'employee_id': employee_id,
                        'laborcategory_id': laborcategory_id,
                        'job_id': job_id if job_id else None,
                        'dateavailable': month,
                        'available_hours': available_hours,
                        'work_hours_percentage': work_hours_percentage
                    })
                    trans.commit()
                    message = "Availability record updated successfully."

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
                logging.error(f"Error during availability update: {e}")
                message = "An error occurred while processing your request."

    except Exception as e:
        logging.error(f"Database error: {e}")
        message = "A database error occurred."

    return redirect(url_for('index', message=message))


@app.route('/view_availability')
def view_availability():
    try:
        with engine.connect() as conn:
            query = """
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
                JOIN Contract c ON pop.contract_id = c.contract_id;
            """
            result = conn.execute(text(query))
            availability_data = result.fetchall()
    except Exception as e:
        logging.error(f"Error fetching availability data: {e}")
        availability_data = []

    return render_template('view_availability.html', data=availability_data)

@app.route('/view_availability_override')
def view_availability_override():
    try:
        with engine.connect() as conn:
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
            print(availability_data)
    except Exception as e:
        logging.error(f"Error fetching availability data: {e}")
        availability_data = []

    return render_template('vew_workavailabilty_override.html', data=availability_data)


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
def update_work_availability_override(record_id):
    data = request.json

    try:
        available_hours = int(data.get('available_hours'))
        work_percentage = float(data.get('work_hours_percentage'))
        
        if not all([available_hours, work_percentage]):
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
def view_employee():
    try:
        with engine.connect() as conn:
            query = """
                SELECT e.employee_id, e.IdFromJamis, e.FirstName, e.LastName, e.Email, e.IsTbd, e.company_id, e.NoteForTbd,
                       es.StartDate, es.EndDate, es.DirectRate
                FROM Employee e
                LEFT JOIN EmployeeSalary es ON e.employee_id = es.employee_id
            """
            result = conn.execute(text(query))
            employees_data = result.fetchall()
            
        return render_template('view_employees.html', data=employees_data)
    except Exception as e:
        logging.error(f"Error fetching employee data: {e}")
        

@app.route('/add_employee', methods=['POST'])
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
        "EndDate": request.form.get('EndDate') if request.form.get('EndDate') != "" else None,
        "DirectRate": request.form['DirectRate']
    }

    with engine.begin() as conn:
        # Insert data into Employee table and get the inserted employee ID
        insert_query = """
            INSERT INTO Employee (IdFromJamis, FirstName, LastName, Email, IsTbd, company_id, NoteForTbd)
            OUTPUT INSERTED.employee_id
            VALUES (:IdFromJamis, :FirstName, :LastName, :Email, :IsTbd, :company_id, :NoteForTbd)
        """
        result = conn.execute(text(insert_query), employee_data)
        employee_id = result.scalar()
        
        if not employee_id:
            return "Error retrieving employee ID", 500

        # Insert data into EmployeeSalary table
        salary_query = """
            INSERT INTO EmployeeSalary (employee_id, StartDate, EndDate, DirectRate)
            VALUES (:employee_id, :StartDate, :EndDate, :DirectRate)
        """
        salary_data['employee_id'] = employee_id
        conn.execute(text(salary_query), salary_data)

    return redirect('/employees')

@app.route('/add_employee_form', methods=['GET'])
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
def generate_file():

    contract_id = request.form.get("contract", None)
    pop_id = request.form.get("pop_id", None)
    work_year = request.form.get("work_year", 2024)
    dc_start_year = request.form.get("dc_start_year", 2023)
    dc_end_year = request.form.get("dc_end_year", 2027)
    file_name = request.form.get("filename", "Combined_spreadsheet.xlsx")
    last_month = request.form.get("last_month", "08/2024")
    
    create_combined_workbook(
        contract_id=contract_id, 
        last_month_str=last_month,
        work_year=work_year,
        filename=file_name,
        dc_start_year=dc_start_year,
        dc_end_year=dc_end_year
    )

    return redirect('/')
        


if __name__ == '__main__':
    app.run(debug=True)