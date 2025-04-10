from datetime import datetime, timedelta
from airflow import DAG
from airflow.operators.bash_operator import BashOperator
from airflow.providers.google.cloud.sensors.gcs import GCSObjectsWithPrefixExistenceSensor
from airflow.providers.google.cloud.operators.dataproc import DataprocSubmitJobOperator
from airflow.utils.dates import days_ago

default_args = {
    'owner': 'airflow',
    'depends_on_past': False,
    'email_on_failure': False,
    'email_on_retry': False,
    'retries': 0,
    'retry_delay': timedelta(minutes=1)
}

dag = DAG(
    'gcsarchive',
    default_args=default_args,
    description='Load logistics data into Hive on GCP Dataproc',
    schedule_interval=timedelta(days=1),
    start_date=days_ago(1),
    tags=['example'],
)

# Sense the new file in GCS
sense_logistics_file = GCSObjectsWithPrefixExistenceSensor(
    task_id='sense_logistics_file',
    bucket='logistics_raw',
    prefix='input_data/logistics_',
    mode='poke',
    timeout=300,
    poke_interval=30,
    dag=dag
)

# Create Hive Database if not exists
create_hive_database = DataprocSubmitJobOperator(
    task_id="create_hive_database",
    job={
        "reference": {
            "project_id": "harshapersonal",
            "job_id": "create-hive-db",
        },
        "placement": {
            "cluster_name": "cluster-c27e",
        },
        "hive_job": {
            "query_list": {
                "queries": [
                    "CREATE DATABASE IF NOT EXISTS logistics_db;"
                ]
            }
        }
    },
    region='us-central1',
    project_id='harshapersonal',
    dag=dag
)

# Create main Hive table
create_hive_table = DataprocSubmitJobOperator(
    task_id="create_hive_table",
    job={
        "reference": {
            "project_id": "harshapersonal",
            "job_id": "create-hive-table",
        },
        "placement": {
            "cluster_name": "cluster-c27e",
        },
        "hive_job": {
            "query_list": {
                "queries": [
                    """CREATE EXTERNAL TABLE IF NOT EXISTS logistics_db.logistics_data (
                        delivery_id INT,
                        `date` STRING,
                        origin STRING,
                        destination STRING,
                        vehicle_type STRING,
                        delivery_status STRING,
                        delivery_time STRING
                    )
                    ROW FORMAT DELIMITED
                    FIELDS TERMINATED BY ','
                    STORED AS TEXTFILE
                    LOCATION 'gs://logistics_raw/input_data/'
                    tblproperties('skip.header.line.count'='1');"""
                ]
            }
        }
    },
    region='us-central1',
    project_id='harshapersonal',
    dag=dag
)

# Create partitioned Hive table
create_partitioned_table = DataprocSubmitJobOperator(
    task_id="create_partitioned_table",
    job={
        "reference": {
            "project_id": "harshapersonal",
            "job_id": "create-partitioned-table",
        },
        "placement": {
            "cluster_name": "cluster-c27e",
        },
        "hive_job": {
            "query_list": {
                "queries": [
                    """CREATE TABLE IF NOT EXISTS logistics_db.logistics_data_partitioned (
                        delivery_id INT,
                        origin STRING,
                        destination STRING,
                        vehicle_type STRING,
                        delivery_status STRING,
                        delivery_time STRING
                    )
                    PARTITIONED BY (`date` STRING)
                    STORED AS TEXTFILE;"""
                ]
            }
        }
    },
    region='us-central1',
    project_id='harshapersonal',
    dag=dag
)

# Set Hive properties for dynamic partitioning and load data
set_hive_properties_and_load_partitioned = DataprocSubmitJobOperator(
    task_id="set_hive_properties_and_load_partitioned",
    job={
        "reference": {
            "project_id": "harshapersonal",
            "job_id": "set-hive-properties",
        },
        "placement": {
            "cluster_name": "cluster-c27e",
        },
        "hive_job": {
            "query_list": {
                "queries": [
                    """SET hive.exec.dynamic.partition = true;
                    SET hive.exec.dynamic.partition.mode = nonstrict;

                    INSERT INTO logistics_db.logistics_data_partitioned PARTITION(`date`)
                    SELECT delivery_id, origin, destination, vehicle_type, delivery_status, delivery_time, `date` 
                    FROM logistics_db.logistics_data;"""
                ]
            }
        }
    },
    region='us-central1',
    project_id='harshapersonal',
    dag=dag
)

# Move processed files to archive bucket
archive_processed_file = BashOperator(
    task_id='archive_processed_file',
    bash_command="gsutil -m mv gs://logistics_raw/input_data/logistics_*.csv gs://logistics_archive/",
    dag=dag
)

# Task dependencies
sense_logistics_file >> create_hive_database >> create_hive_table >> create_partitioned_table >> set_hive_properties_and_load_partitioned >> archive_processed_file
