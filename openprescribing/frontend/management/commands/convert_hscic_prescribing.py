import datetime
import logging
import os

from django.conf import settings
from django.core.management.base import BaseCommand
from django.core.management.base import CommandError

from gcutils.bigquery import Client, TableExporter

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    """Processes manually-fetched detailed prescribing information CSV.

    * Appends prescribing information to the hscic.prescribing table in BQ
    * Downloads a converted CSV file for importing into relevant partition of
      frontend_partition table in Postgres by import_hscic_prescribing task

    We assume that the source CSV file has been uploaded to Cloud Storage, at
    hscic/prescribing/{year_and_month}/{filename}.csv.

    The source CSV file contains "figures on the number of prescription items
    that are dispensed each month and information relating to costs for English
    practices".

    The source CSV file has one line for each presentation and pack size, (so
    prescriptions of 28 paracetamol will be on a separate line from
    prescriptions of 100 paracetamol).

    The converted CSV file has one line for paracetamol of any pack size.
    """

    def add_arguments(self, parser):
        parser.add_argument('--filename')

    def handle(self, *args, **options):
        path = options['filename']
        converted_path = '{}_formatted.CSV'.format(os.path.splitext(path)[0])
        head, filename = os.path.split(path)
        _, year_and_month = os.path.split(head)

        date = year_and_month + '_01'
        try:
            datetime.datetime.strptime(date, '%Y_%m_%d')
        except ValueError as e:
            message = ('The file path must have a YYYY_MM '
                       'date component in the containing directory: ')
            message += path
            raise CommandError(message)

        hscic_dataset_client = Client(settings.BQ_HSCIC_DATASET)
        tmp_dataset_client = Client(settings.BQ_TMP_DATASET)

        # Check that we haven't already processed data for this month
        sql = '''SELECT COUNT(*)
        FROM {dataset}.prescribing
        WHERE month = TIMESTAMP('{date}')'''.format(
            dataset=hscic_dataset_client.dataset_name,
            date=date.replace('_', '-'),
        )
        results = hscic_dataset_client.query(sql)
        assert results.rows[0][0] == 0

        # Create BQ table backed backed by uploaded source CSV file
        raw_data_table_name = 'raw_prescribing_data_{}'.format(year_and_month)
        gcs_path = 'hscic/prescribing/{}/{}'.format(year_and_month, filename)
        schema = [
            {'name': 'Regional_Office_Name', 'type': 'string'},
            {'name': 'Regional_Office_Code', 'type': 'string'},
            {'name': 'Area_Team_Name', 'type': 'string'},
            {'name': 'Area_Team_Code', 'type': 'string', 'mode': 'required'},
            {'name': 'PCO_Name', 'type': 'string'},
            {'name': 'PCO_Code', 'type': 'string'},
            {'name': 'Practice_Name', 'type': 'string'},
            {'name': 'Practice_Code', 'type': 'string', 'mode': 'required'},
            {'name': 'BNF_Code', 'type': 'string', 'mode': 'required'},
            {'name': 'BNF_Description', 'type': 'string', 'mode': 'required'},
            {'name': 'Items', 'type': 'integer', 'mode': 'required'},
            {'name': 'Quantity', 'type': 'integer', 'mode': 'required'},
            {'name': 'ADQ_Usage', 'type': 'float'},
            {'name': 'NIC', 'type': 'float', 'mode': 'required'},
            {'name': 'Actual_Cost', 'type': 'float', 'mode': 'required'},
        ]
        raw_data_table = tmp_dataset_client.create_storage_backed_table(
            raw_data_table_name,
            schema,
            gcs_path
        )

        # Append aggregated data to prescribing table
        sql = '''
         SELECT
          Area_Team_Code AS sha,
          LEFT(PCO_Code, 3) AS pct,
          Practice_Code AS practice,
          BNF_Code AS bnf_code,
          BNF_Description AS bnf_name,
          SUM(Items) AS items,
          SUM(NIC) AS net_cost,
          SUM(Actual_Cost) AS actual_cost,
          SUM(Quantity * Items) AS quantity,
          TIMESTAMP('%s') AS month,
         FROM %s
         WHERE Practice_Code NOT LIKE '%%998'  -- see issue #349
         GROUP BY
           bnf_code, bnf_name, pct,
           practice, sha
        ''' % (date.replace('_', '-'), raw_data_table.qualified_name)

        prescribing_table = hscic_dataset_client.get_table('prescribing')
        prescribing_table.insert_rows_from_query(
            sql,
            legacy=True,
            write_disposition='WRITE_APPEND'
        )

        # Write aggregated data to new table, for download
        sql = '''
         SELECT
          LEFT(PCO_Code, 3) AS pct_id,
          Practice_Code AS practice_code,
          BNF_Code AS presentation_code,
          SUM(Items) AS total_items,
          SUM(NIC) AS net_cost,
          SUM(Actual_Cost) AS actual_cost,
          SUM(Quantity * Items) AS quantity,
          '%s' AS processing_date,
         FROM %s
         WHERE Practice_Code NOT LIKE '%%998'  -- see issue #349
         GROUP BY
           presentation_code, pct_id, practice_code
        ''' % (date, raw_data_table.qualified_name)

        formatted_data_table_name = 'formatted_prescribing_%s' % year_and_month
        formatted_data_table = tmp_dataset_client.get_table(formatted_data_table_name)
        formatted_data_table.insert_rows_from_query(sql, legacy=True)

        # Export new table to storage, and download
        exporter = TableExporter(formatted_data_table, gcs_path + '_formatted-')
        exporter.export_to_storage(print_header=False)

        with open(converted_path, 'w') as f:
            exporter.download_from_storage_and_unzip(f)
