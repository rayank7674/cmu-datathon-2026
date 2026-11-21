# Databricks notebook source
# MAGIC %md
# MAGIC # Task D — independent external data ingestion and conservative matching
# MAGIC **Run after notebooks 01–06.** Writes only `workspace.vf_bronze.ext_*` and `workspace.vf_silver.ext_*` tables. Never edits source facilities, notebooks 01–06, or Gold.
# MAGIC 
# MAGIC Prerequisites: `workspace.vf_silver.facilities`, volume `/Volumes/workspace/vf_bronze/external/`, Python+Spark notebook compute. Optional files in `pmjay/`, `nabh/`, `eraktkosh/`, `hmis/`, `gov_hospital_directory/`, `gov_health_centres/`, `cghs/`. Each source directory may contain `manifest.json` with `source_name`, `source_url`, `publication_date`, `downloaded_at`, `file_url` (download URL optional). **Never fabricate missing metadata.** CSV, TSV, XLSX accepted; PDF is NOT silently parsed.
# MAGIC 
# MAGIC Run cells in order. A source without an authorized downloadable facility-level file is recorded in `ext_source_status` and is NOT ingested. When outbound Wikidata access fails, download SPARQL JSON manually to `wikidata/hospitals.json` and rerun. Keep PM-JAY empanelment, NABH accreditation, and blood-centre listing separate from proof of any clinical service.

# COMMAND ----------
import re, json, os, hashlib, urllib.parse, urllib.request, datetime
from pyspark.sql import functions as F, Window, types as T

CAT='workspace'
ROOT='/Volumes/workspace/vf_bronze/external'
NOW=datetime.datetime.now(datetime.timezone.utc)
SOURCES={
 'pmjay':('PM-JAY','https://hospitals.pmjay.gov.in/'),
 'nabh':('NABH','https://nabh.co/find-a-healthcare-organisation/'),
 'eraktkosh':('eRaktKosh','https://eraktkosh.mohfw.gov.in/eraktkoshPortal/'),
 'hmis':('HMIS','https://hmis.mohfw.gov.in/'),
 'gov_hospital_directory':('National Hospital Directory','https://www.data.gov.in/'),
 'gov_health_centres':('Government Health Centres Directory','https://www.data.gov.in/'),
 'cghs':('CGHS','https://cghs.mohfw.gov.in/'),
}
# HMIS only permitted after the input has been confirmed to be facility-by-period.
REQUIRE_HMIS_COLUMNS=True

spark.sql('SELECT COUNT(*) AS n FROM workspace.vf_silver.facilities').show()

# COMMAND ----------
# Fixed schemas; all created tables have ext_ names. Dates below are source dates, not import/verification timestamps.
status_schema=T.StructType([
 T.StructField('source',T.StringType()),T.StructField('source_name',T.StringType()),
 T.StructField('source_url',T.StringType()),T.StructField('status',T.StringType()),
 T.StructField('rows_loaded',T.LongType()),T.StructField('publication_date',T.DateType()),
 T.StructField('downloaded_at',T.TimestampType()),T.StructField('checked_at',T.TimestampType()),
 T.StructField('reason',T.StringType()),T.StructField('manual_action',T.StringType()),
 T.StructField('login_needed',T.StringType())])
raw_schema=T.StructType([
 T.StructField('source',T.StringType()),T.StructField('source_name',T.StringType()),
 T.StructField('source_record_id',T.StringType()),T.StructField('raw_json',T.StringType()),
 T.StructField('source_file',T.StringType()),T.StructField('source_url',T.StringType()),
 T.StructField('publication_date',T.DateType()),T.StructField('downloaded_at',T.TimestampType()),
 T.StructField('imported_at',T.TimestampType()),T.StructField('source_type',T.StringType()),
 T.StructField('source_ref',T.StringType()),T.StructField('confidence',T.StringType())])
clean_schema=T.StructType([
 T.StructField('source',T.StringType()),T.StructField('source_record_id',T.StringType()),
 T.StructField('name',T.StringType()),T.StructField('name_raw',T.StringType()),
 T.StructField('pincode',T.StringType()),T.StructField('pincode_raw',T.StringType()),
 T.StructField('state',T.StringType()),T.StructField('district',T.StringType()),
 T.StructField('latitude',T.DoubleType()),T.StructField('longitude',T.DoubleType()),
 T.StructField('latitude_raw',T.StringType()),T.StructField('longitude_raw',T.StringType()),
 T.StructField('beds',T.IntegerType()),T.StructField('beds_raw',T.StringType()),
 T.StructField('inception_raw',T.StringType()),T.StructField('source_name',T.StringType()),
 T.StructField('source_url',T.StringType()),T.StructField('publication_date',T.DateType()),
 T.StructField('downloaded_at',T.TimestampType()),T.StructField('source_type',T.StringType()),
 T.StructField('source_ref',T.StringType()),T.StructField('confidence',T.StringType()),
 T.StructField('quality_reason',T.StringType())])

# COMMAND ----------
# Verified NSS 80th round (survey Jan-Dec 2025). This is a REFERENCE, not a facility quote or price.
NSS_URL='https://nhm.tn.gov.in/wp-content/uploads/2026/Key_Statistics/NSSO/NSSO%20Health%2080th.pdf'
PIB_URL='https://www.pib.gov.in/PressReleasePage.aspx?PRID=2253752&lang=1&reg=22'
# Table 2.20 national averages + medians, non-childbirth. Source verified against MoSPI press release and NSS Report 596.
cost_rows=[
 ('public',6631,1100),('charitable',39530,10000),('private',50508,24000),('all',34064,11285)
]
cost_data=[]
for ownership,mean,median in cost_rows:
 for statistic,amount in [('mean_oope',mean),('median_oope',median)]:
  cost_data.append((
   'NSS_80_2025',ownership,'all_india','hospitalisation_excluding_childbirth',statistic,
   float(amount),'INR','NSS Report 596, Table 2.20','2025-01-01 through 2025-12-31',
   'Household survey estimate; not hospital-specific; survey excludes childbirth for this indicator',
   'NSS 80th round Household Social Consumption: Health',NSS_URL,None,None,
   'external',NSS_URL,'high'))
cost_schema=['survey_id','facility_sector','geography','care_category','statistic','amount','currency','table_reference','survey_period','limitation','source_name','source_url','publication_date','downloaded_at','source_type','source_ref','confidence']
# No publication date guessed: source report date metadata must be verified separately.
spark.createDataFrame(cost_data, T.StructType([T.StructField(k, (T.DoubleType() if k=='amount' else T.DateType() if k=='publication_date' else T.TimestampType() if k=='downloaded_at' else T.StringType()),True) for k in cost_schema])).write.mode('overwrite').option('overwriteSchema','true').saveAsTable('workspace.vf_bronze.ext_cost_reference')

# COMMAND ----------
# Wikidata SPARQL: P31/P279 hospital; P17 India; P625 coordinates; P571 inception; P6801 hospital beds.
SPARQL='''SELECT DISTINCT ?item ?itemLabel ?coord ?inception ?beds WHERE {
  ?item wdt:P17 wd:Q668 ; wdt:P31/wdt:P279* wd:Q16917 .
  OPTIONAL { ?item wdt:P625 ?coord . }
  OPTIONAL { ?item wdt:P571 ?inception . }
  OPTIONAL { ?item wdt:P6801 ?beds . }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en,hi" . }
} LIMIT 20000'''
ENDPOINT='https://query.wikidata.org/sparql'
QUERY_URL=ENDPOINT+'?'+urllib.parse.urlencode({'query':SPARQL,'format':'json'})

def wd_bindings(doc):
 if not isinstance(doc,dict) or 'results' not in doc: raise ValueError('Expected Wikidata SPARQL JSON bindings')
 return doc['results']['bindings']

def parse_point(s):
 m=re.search(r'Point\(\s*(-?[0-9.]+)\s+(-?[0-9.]+)\s*\)',s or '')
 if not m:return (None,None)
 lon,lat=float(m.group(1)),float(m.group(2))
 return (lat,lon) if 6<=lat<=37.5 and 68<=lon<=97.5 else (None,None)

def get_val(b,k): return b.get(k,{}).get('value')

wd_error=None
wd_doc=None
try:
 req=urllib.request.Request(QUERY_URL,headers={'User-Agent':'CMUDatathonResearch/1.0 (academic facility matching)','Accept':'application/sparql-results+json'})
 with urllib.request.urlopen(req,timeout=70) as resp: wd_doc=json.load(resp)
except Exception as ex:
 wd_error=f'{type(ex).__name__}: {str(ex)[:300]}'
 # Upload SPARQL JSON as /Volumes/workspace/vf_bronze/external/wikidata/hospitals.json when serverless egress is blocked.
 try:
  with open(ROOT+'/wikidata/hospitals.json','r',encoding='utf8') as handle: wd_doc=json.load(handle)
 except Exception: pass

wd_rows=[]
if wd_doc is not None:
 byid={}
 for b in wd_bindings(wd_doc):
  uri=get_val(b,'item')
  if not uri or not re.search(r'/Q\d+$',uri): continue
  qid=uri.rsplit('/',1)[-1]
  o=byid.setdefault(qid,{'wikidata_id':qid,'label':None,'coordinate_wkt':None,'inception_raw':None,'beds_raw':None,'values_conflict':False})
  for key,val in [('label',get_val(b,'itemLabel')),('coordinate_wkt',get_val(b,'coord')),('inception_raw',get_val(b,'inception')),('beds_raw',get_val(b,'beds'))]:
   if val is not None:
    if o[key] is not None and o[key]!=val:o['values_conflict']=True
    else:o[key]=val
 for qid,o in byid.items():
  lat,lon=parse_point(o['coordinate_wkt'])
  wd_rows.append((qid,o['label'],o['coordinate_wkt'],lat,lon,o['inception_raw'],o['beds_raw'],o['values_conflict'],
    'Wikidata',QUERY_URL,None,None,NOW,'external','https://www.wikidata.org/wiki/'+qid,'medium' if not o['values_conflict'] else 'low'))
wd_schema=['wikidata_id','label','coordinate_wkt','latitude','longitude','inception_raw','beds_raw','values_conflict',
           'source_name','source_url','publication_date','downloaded_at','imported_at','source_type','source_ref','confidence']
if wd_rows:
 spark.createDataFrame(wd_rows,T.StructType([T.StructField(k, T.DoubleType() if k in ('latitude','longitude') else T.BooleanType() if k=='values_conflict' else T.DateType() if k=='publication_date' else T.TimestampType() if k in ('downloaded_at','imported_at') else T.StringType(),True) for k in wd_schema])).write.mode('overwrite').option('overwriteSchema','true').saveAsTable('workspace.vf_bronze.ext_wikidata_hospitals')
 print('Wikidata records:',len(wd_rows))
else:
 print('Wikidata download not available; existing Wikidata bronze snapshot (if any) left untouched; cause:',wd_error)

# COMMAND ----------
# Upload reader. Existing notebook 05 is NEVER edited. Raw JSON preserves original string values.
def norm_col(s):return re.sub(r'_+','_',re.sub('[^a-z0-9]+','_',s.lower())).strip('_')
ALIASES={
 'name':['hospital_name','name_of_hospital','name_of_the_hospital','facility_name','name_of_facility','name','blood_bank_name','blood_center_name','organisation_name','organization_name','hospital'],
 'pincode':['pincode','pin_code','pin','postcode','zip','postal_code'],
 'state':['state','state_name','state_ut'], 'district':['district','district_name'],
 'latitude':['latitude','lat'],'longitude':['longitude','lon','lng','long'],
 'beds':['number_of_beds','no_of_beds','total_beds','bed_count','beds'],
 'facility_id':['hospital_id','facility_id','hfr_id','id','accreditation_no','registration_no'],
 'period':['reporting_month','month','report_month','reporting_period','year_month'],
 'deliveries':['deliveries','total_deliveries','deliveries_conducted_at_facility'],
 'c_sections':['c_section','c_sections','caesarean_sections','cesarean_sections'],
 'dialysis':['dialysis','dialysis_sessions']}

def column(frame,key):
 idx={norm_col(c):c for c in frame.columns}
 for alias in ALIASES[key]:
  if alias in idx:return F.col('`'+idx[alias].replace('`','``')+'`').cast('string')
 return F.lit(None).cast('string')

def read_file(p):
 ext=p.lower().rsplit('.',1)[-1]
 if ext in ('csv','tsv','txt'):
  return spark.read.option('header','true').option('multiLine','true').option('escape','"').option('sep','\t' if ext=='tsv' else ',').csv(p)
 if ext=='xlsx':
  import pandas as pd
  pdf=pd.read_excel(p,dtype=str).fillna('')
  if len(pdf)==0:return None
  return spark.createDataFrame(pdf.astype(str))
 return None

def read_manifest(folder):
 try:
  with open(folder+'/manifest.json',encoding='utf8') as f:return json.load(f)
 except Exception:return {}

def parse_date(v):
 try:return datetime.date.fromisoformat(str(v)[:10]) if v else None
 except Exception:return None

def parse_ts(v):
 try:return datetime.datetime.fromisoformat(str(v).replace('Z','+00:00')) if v else None
 except Exception:return None

raw_frames=[];clean_frames=[];activity_frames=[];statuses=[]
for source,(source_name,default_url) in SOURCES.items():
 folder=ROOT+'/'+source
 try: files=[x.path for x in dbutils.fs.ls(folder) if not x.isDir() and x.name.lower().endswith(('.csv','.tsv','.txt','.xlsx'))]
 except Exception:files=[]
 manifest=read_manifest(folder)
 url=manifest.get('source_url') or default_url
 publication_date=parse_date(manifest.get('publication_date'))
 downloaded_at=parse_ts(manifest.get('downloaded_at'))
 count=0; problems=[]
 for p in files:
  try:
   frame=read_file(p)
   if frame is None:continue
   if source=='hmis' and REQUIRE_HMIS_COLUMNS:
    needed=['facility_id','period']
    if not all(any(norm_col(c)==a for c in frame.columns for a in ALIASES[k]) for k in needed):
     problems.append(os.path.basename(p)+': missing facility identifier or reporting period');continue
    if not any(any(norm_col(c)==a for c in frame.columns) for k in ('deliveries','c_sections','dialysis') for a in ALIASES[k]):
     problems.append(os.path.basename(p)+': no identifiable facility service activity columns');continue
   # Preserve all raw columns as a JSON document; hashes are stable across reruns. Identical duplicate rows remain in bronze.
   name=column(frame,'name');pin=column(frame,'pincode');lat=column(frame,'latitude');lon=column(frame,'longitude');beds=column(frame,'beds')
   raw=frame.select(F.to_json(F.struct(*[F.col('`'+c.replace('`','``')+'`').alias(c) for c in frame.columns])).alias('raw_json'))
   raw=raw.withColumn('source_record_id',F.sha2(F.concat_ws('|',F.lit(source),F.lit(p),F.col('raw_json')),256))
   raw=raw.withColumn('source',F.lit(source)).withColumn('source_name',F.lit(source_name))\
     .withColumn('source_file',F.lit(p)).withColumn('source_url',F.lit(url))\
     .withColumn('publication_date',F.lit(publication_date).cast('date'))\
     .withColumn('downloaded_at',F.lit(downloaded_at).cast('timestamp'))\
     .withColumn('imported_at',F.current_timestamp()).withColumn('source_type',F.lit('external'))\
     .withColumn('source_ref',F.lit(p)).withColumn('confidence',F.lit('medium'))
   raw_frames.append(raw.select(*raw_schema.fieldNames()))
   # Standardize using original columns, NOT inferred  values; retain raw beside typed values.
   basic=frame.select(name.alias('name_raw'),pin.alias('pincode_raw'),column(frame,'state').alias('state'),
      column(frame,'district').alias('district'),lat.alias('latitude_raw'),lon.alias('longitude_raw'),beds.alias('beds_raw'),
      F.to_json(F.struct(*[F.col('`'+c.replace('`','``')+'`').alias(c) for c in frame.columns])).alias('raw_json'))
   basic=basic.withColumn('name',F.trim('name_raw'))\
     .withColumn('pincode',F.when(F.trim('pincode_raw').rlike('^[1-9][0-9]{5}$'),F.trim('pincode_raw')))\
     .withColumn('latitude',F.when(F.col('latitude_raw').cast('double').between(6,37.5),F.col('latitude_raw').cast('double')))\
     .withColumn('longitude',F.when(F.col('longitude_raw').cast('double').between(68,97.5),F.col('longitude_raw').cast('double')))\
     .withColumn('beds',F.when(F.col('beds_raw').rlike('^[0-9]{1,5}$') & F.col('beds_raw').cast('int').between(1,10000),F.col('beds_raw').cast('int')))
   basic=basic.withColumn('source_record_id',F.sha2(F.concat_ws('|',F.lit(source),F.lit(p),F.col('raw_json')),256))
   basic=basic.withColumn('source',F.lit(source)).withColumn('source_name',F.lit(source_name))\
      .withColumn('source_url',F.lit(url)).withColumn('publication_date',F.lit(publication_date).cast('date'))\
      .withColumn('downloaded_at',F.lit(downloaded_at).cast('timestamp'))\
      .withColumn('inception_raw',F.lit(None).cast('string')).withColumn('source_type',F.lit('external'))\
      .withColumn('source_ref',F.lit(p)).withColumn('confidence',F.lit('medium'))\
      .withColumn('quality_reason',F.when(F.col('name').isNull() | (F.col('name')==''),'missing name')
        .when(F.col('latitude').isNull() & F.col('longitude').isNotNull(),'incomplete coordinates')
        .when(F.col('latitude').isNotNull() & F.col('longitude').isNull(),'incomplete coordinates'))
   clean_frames.append(basic.select(*clean_schema.fieldNames()))
   if source=='hmis':
    # Facility-wise *observed historical activity* only; never infer current service readiness.
    activity=frame.select(column(frame,'facility_id').alias('source_facility_id'),
      column(frame,'name').alias('source_facility_name'),column(frame,'period').alias('reporting_period'),
      column(frame,'deliveries').alias('deliveries_raw'),column(frame,'c_sections').alias('c_sections_raw'),
      column(frame,'dialysis').alias('dialysis_raw'),
      F.to_json(F.struct(*[F.col('`'+c.replace('`','``')+'`').alias(c) for c in frame.columns])).alias('raw_json'))
    activity=activity.withColumn('source_record_id',F.sha2(F.concat_ws('|',F.lit(source),F.lit(p),F.col('raw_json')),256))
    for metric in ('deliveries','c_sections','dialysis'):
     activity=activity.withColumn(metric,F.when(F.col(metric+'_raw').rlike('^[0-9]{1,9}$'),F.col(metric+'_raw').cast('long')))
    activity=activity.withColumn('source',F.lit(source)).withColumn('source_name',F.lit(source_name))      .withColumn('source_url',F.lit(url)).withColumn('publication_date',F.lit(publication_date).cast('date'))      .withColumn('downloaded_at',F.lit(downloaded_at).cast('timestamp')).withColumn('source_type',F.lit('external'))      .withColumn('source_ref',F.lit(p)).withColumn('confidence',F.lit('medium'))      .withColumn('interpretation',F.lit('Past reported activity, not verification of current equipment, personnel, or emergency availability'))
    activity_frames.append(activity.select('source','source_record_id','source_facility_id','source_facility_name','reporting_period',
      'deliveries','deliveries_raw','c_sections','c_sections_raw','dialysis','dialysis_raw',
      'source_name','source_url','publication_date','downloaded_at','source_type','source_ref','confidence','interpretation'))
   count+=frame.count()
  except Exception as ex: problems.append(os.path.basename(p)+': '+type(ex).__name__+' '+str(ex)[:200])
 if count>0:status='loaded'
 elif problems:status='blocked'
 else:status='not_downloaded'
 manual={
 'pmjay':'Download official dated state/national roster from NHA HEM or state agency; include hospital ID, name, state, postcode and specialties; place CSV/XLSX in pmjay/ with manifest.json.',
 'nabh':'From NABH accreditation directory obtain authorized dated accreditation register, including certificate scope and expiry; export CSV/XLSX to nabh/ with manifest.json.',
 'eraktkosh':'Request authorized downloadable blood-centre directory; do not automate scraping interactive blood-stock/live-status pages.',
 'hmis':'Obtain facility-ID + month + deliveries/C-section/dialysis export from HMIS operator. Quarterly min/max or state totals are not facility-level data.',
 'cghs':'Download official dated CGHS empanelled hospitals roster and upload to cghs/.',
 'gov_hospital_directory':'Download AIKosh national hospital directory and upload CSV/XLSX.',
 'gov_health_centres':'Download national/state health-centre roster and upload CSV/XLSX.'}[source]
 login={'pmjay':'possible for export','nabh':'possible for export','eraktkosh':'possible for export','hmis':'likely for full facility records'}.get(source,'possibly')
 statuses.append((source,source_name,url,status,count,publication_date,downloaded_at,NOW,'; '.join(problems) if problems else ('No eligible downloadable file provided' if count==0 else None),manual,login))
 print(source,status,count,problems[:2])

# COMMAND ----------
# Bronze: per-source raw tables, only if an actual file was loaded.
if raw_frames:
 allraw=raw_frames[0]
 for part in raw_frames[1:]:allraw=allraw.unionByName(part)
 for source in SOURCES:
  subset=allraw.filter(F.col('source')==source)
  if subset.limit(1).count()>0:
   subset.write.mode('overwrite').option('overwriteSchema','true').saveAsTable(f'workspace.vf_bronze.ext_{source}_raw')
else: print('No external local CSV/TSV/XLSX provided; existing bronze raw ext_ tables not touched.')

# COMMAND ----------
# Wikidata raw+clean; only replaced on successful retrieval. For beds, leave NULL if conflicting values.
if wd_rows:
 df=spark.table('workspace.vf_bronze.ext_wikidata_hospitals')
 c=df.select(F.lit('wikidata').alias('source'),F.col('wikidata_id').alias('source_record_id'),
   F.col('label').alias('name'),F.col('label').alias('name_raw'),F.lit(None).cast('string').alias('pincode'),
   F.lit(None).cast('string').alias('pincode_raw'),F.lit(None).cast('string').alias('state'),
   F.lit(None).cast('string').alias('district'),'latitude','longitude',
   F.col('coordinate_wkt').alias('latitude_raw'),F.col('coordinate_wkt').alias('longitude_raw'),
   F.when(~F.col('values_conflict') & F.col('beds_raw').rlike('^[0-9]{1,5}$') & F.col('beds_raw').cast('int').between(1,10000),F.col('beds_raw').cast('int')).alias('beds'),
   'beds_raw','inception_raw','source_name','source_url','publication_date','downloaded_at','source_type','source_ref','confidence',
   F.when(F.col('values_conflict'),'conflicting Wikidata statements; numerical value suppressed').alias('quality_reason'))
 clean_frames.append(c.select(*clean_schema.fieldNames()))
 statuses.append(('wikidata','Wikidata',QUERY_URL,'loaded',len(wd_rows),None,None,NOW,None,'No manual action required','no'))
else:
 statuses.append(('wikidata','Wikidata',QUERY_URL,'blocked',0,None,None,NOW,wd_error or 'No Wikidata response','Open Wikidata Query Service and export JSON from the supplied SPARQL query; upload wikidata/hospitals.json.','no'))

if clean_frames:
 combined=clean_frames[0]
 for p in clean_frames[1:]:combined=combined.unionByName(p)
 # Repeated identical rows retain their full raw JSON in bronze; silver intentionally unique by source+record ID.
 combined=combined.withColumn('rn',F.row_number().over(Window.partitionBy('source','source_record_id').orderBy(F.col('source_ref')))).filter('rn=1').drop('rn')
 combined.write.mode('overwrite').option('overwriteSchema','true').saveAsTable('workspace.vf_silver.ext_facilities_clean')
else:
 spark.createDataFrame([],clean_schema).write.mode('overwrite').option('overwriteSchema','true').saveAsTable('workspace.vf_silver.ext_facilities_clean')
 print('No external facility rows: created empty clean table (0 rows).')

if activity_frames:
 activities=activity_frames[0]
 for part in activity_frames[1:]:activities=activities.unionByName(part)
 activities.dropDuplicates(['source','source_record_id']).write.mode('overwrite').option('overwriteSchema','true').saveAsTable('workspace.vf_silver.ext_hmis_activity')

spark.createDataFrame(statuses,status_schema).write.mode('overwrite').option('overwriteSchema','true').saveAsTable('workspace.vf_silver.ext_source_status')

# COMMAND ----------
# Conservative matcher: geographical/pincode blocking + informative name similarity.
# No service is verified by these matches. Every accepted match is one-per-facility AND source;
# an external record competing for two facilities is made ambiguous, not auto-attached.
if spark.catalog.tableExists('workspace.vf_silver.ext_facilities_clean'):
 f=spark.table('workspace.vf_silver.facilities')
 e=spark.table('workspace.vf_silver.ext_facilities_clean')
 def find_existing(names,possible):
  return next((a for a in possible if a in names),None)
 fcols=f.columns
 name_col=find_existing(fcols,['name']); zip_col=find_existing(fcols,['pincode','zip','address_zipOrPostcode'])
 lat_col=find_existing(fcols,['latitude']);lon_col=find_existing(fcols,['longitude'])
 if not all([name_col,lat_col,lon_col]):raise RuntimeError('Expected facility name/latitude/longitude columns missing; inspect schema before matching')
 f=f.select('facility_id',F.col(name_col).alias('facility_name'),
    F.col(lat_col).cast('double').alias('flat'),F.col(lon_col).cast('double').alias('flon'),
    (F.col(zip_col).cast('string') if zip_col else F.lit(None).cast('string')).alias('fpin'))
 e=e.select('source','source_record_id',F.col('name').alias('ext_name'),F.col('latitude').alias('elat'),
     F.col('longitude').alias('elon'),F.col('pincode').alias('epin'),'source_ref','source_type','confidence')
 f=f.withColumn('fpin',F.when(F.col('fpin').rlike('^[1-9][0-9]{5}$'),'fpin'))
 # Split generic tokens out so 'General Hospital' vs 'General Hospital' cannot match without distinctive name words.
 STOP=['hospital','hospitals','health','healthcare','medical','centre','center','clinic','clinics','and','the','of','private','govt','government','general','district','civil','nursing','home','care','trust','limited','ltd','pvt','india','multispeciality','multispecialty','institute']
 def add_norm(df,prefix,col):
  n=F.trim(F.regexp_replace(F.lower(F.col(col)),'[^a-z0-9]+',' '))
  return df.withColumn(prefix+'norm',n).withColumn(prefix+'tok',F.array_distinct(F.filter(F.split(n,' +'),lambda z:(F.length(z)>2) & (~F.array_contains(F.array(*[F.lit(v) for v in STOP]),z)))))
 f=add_norm(f,'f','facility_name');e=add_norm(e,'e','ext_name')
 f=f.withColumn('flatbin',F.floor(F.col('flat')*20)).withColumn('flonbin',F.floor(F.col('flon')*20))
 e=e.withColumn('elatbin',F.floor(F.col('elat')*20)).withColumn('elonbin',F.floor(F.col('elon')*20))
 # Blocking includes pincode equality OR nearby (within ~2.5km lat box), with geographic prefilter.
 # Spark join plan scales by bins; do not do countrywide Cartesian matching.
 fc=f.select('*',F.explode(F.array(F.struct(F.lit(0).alias('dlat'),F.lit(0).alias('dlon')),
  *[F.struct(F.lit(a).alias('dlat'),F.lit(b).alias('dlon')) for a in (-1,0,1) for b in (-1,0,1) if (a,b)!=(0,0)])).alias('offset'))\
  .withColumn('joinlat',F.col('flatbin')+F.col('offset.dlat')).withColumn('joinlon',F.col('flonbin')+F.col('offset.dlon')).drop('offset')
 geo=fc.join(e,(F.col('joinlat')==F.col('elatbin'))&(F.col('joinlon')==F.col('elonbin')),'inner')
 # Pincode path also captures matches without coordinates; using non-null postcodes avoids null matching.
 bypin=f.join(e,(F.col('fpin').isNotNull())&(F.col('epin').isNotNull())&(F.col('fpin')==F.col('epin')),'inner')
 # Normalize compatible columns after separate paths.
 base=['facility_id','facility_name','fpin','flat','flon','fnorm','ftok','source','source_record_id','ext_name','epin','elat','elon','enorm','etok','source_ref','source_type','confidence']
 pairs=geo.select(*base).unionByName(bypin.select(*base)).dropDuplicates(['facility_id','source','source_record_id'])
 pairs=pairs.withColumn('distance_km',F.when(F.col('flat').isNotNull()&F.col('flon').isNotNull()&F.col('elat').isNotNull()&F.col('elon').isNotNull(),
  2*6371*F.asin(F.least(F.lit(1.0),F.sqrt(F.pow(F.sin(F.radians(F.col('elat')-F.col('flat'))/2),2)+
  F.cos(F.radians('flat'))*F.cos(F.radians('elat'))*F.pow(F.sin(F.radians(F.col('elon')-F.col('flon'))/2),2))))))
 pairs=pairs.filter((F.col('distance_km')<=2.0)|((F.col('fpin')==F.col('epin'))&F.col('fpin').isNotNull()))
 pairs=pairs.withColumn('pin_agree',F.col('fpin').isNotNull()&F.col('epin').isNotNull()&(F.col('fpin')==F.col('epin')))
 pairs=pairs.withColumn('pin_conflict',F.col('fpin').isNotNull()&F.col('epin').isNotNull()&(F.col('fpin')!=F.col('epin')))
 pairs=pairs.withColumn('geometry_conflict',F.col('distance_km').isNotNull()&(F.col('distance_km')>2.0))
 pairs=pairs.withColumn('tok_sim',F.when(F.size(F.array_union('ftok','etok'))>0,F.size(F.array_intersect('ftok','etok'))/F.size(F.array_union('ftok','etok'))).otherwise(F.lit(0.0)))
 pairs=pairs.withColumn('str_sim',F.when(F.greatest(F.length('fnorm'),F.length('enorm'))>0,
 1-F.levenshtein('fnorm','enorm')/F.greatest(F.length('fnorm'),F.length('enorm'))).otherwise(F.lit(0.0)))
 pairs=pairs.withColumn('name_sim',F.round(F.greatest(F.col('tok_sim'),F.col('str_sim')*F.lit(0.95)),4))
 pairs=pairs.withColumn('match_score',F.round(F.lit(.7)*F.col('name_sim')+
  F.lit(.2)*F.when(F.col('distance_km')<=.5,1).when(F.col('distance_km')<=2,.7).otherwise(0)+
  F.lit(.1)*F.when(F.col('pin_agree'),1).otherwise(0),4))
 pairs=pairs.withColumn('eligible',(~F.col('pin_conflict')) & (~F.col('geometry_conflict')) & (F.size('ftok')>0) & (F.size('etok')>0) &
  (((F.col('distance_km')<=.5)&(F.col('name_sim')>=.80))|
  ((F.col('distance_km')<=2)&(F.col('name_sim')>=.87))|
  (F.col('pin_agree')&(F.col('name_sim')>=.88))))
 # All candidate pairs saved for audit; accepted table will have one row per facility/source.
 pairs=pairs.withColumn('matched_fields',F.array_compact(F.array(F.when(F.col('name_sim')>=.8,F.lit('name')),
  F.when(F.col('distance_km')<=2,F.lit('coordinates')),F.when(F.col('pin_agree'),F.lit('pincode')))))
 rank_w=Window.partitionBy('facility_id','source').orderBy(F.col('eligible').desc(),F.col('match_score').desc(),F.col('source_record_id'))
 ext_w=Window.partitionBy('source','source_record_id').orderBy(F.col('eligible').desc(),F.col('match_score').desc(),F.col('facility_id'))
 pairs=pairs.withColumn('rank_in_facility',F.row_number().over(rank_w))\
  .withColumn('second_best_score',F.lead('match_score').over(rank_w))\
  .withColumn('rank_for_external',F.row_number().over(ext_w))\
  .withColumn('external_second_score',F.lead('match_score').over(ext_w))
 pairs=pairs.withColumn('match_status',F.when(~F.col('eligible'),'rejected')
  .when((F.col('rank_in_facility')>1)|(F.col('rank_for_external')>1),'ambiguous')
  .when((F.col('second_best_score').isNotNull()) & ((F.col('match_score')-F.col('second_best_score'))<.08),'ambiguous')
  .when((F.col('external_second_score').isNotNull()) & ((F.col('match_score')-F.col('external_second_score'))<.08),'ambiguous')
  .when((F.col('distance_km')<=.5)&(F.col('name_sim')>=.90)&(~F.col('pin_conflict')),'accepted_high')
  .otherwise('accepted_medium'))
 pairs=pairs.withColumn('match_method',F.when(F.col('pin_agree')&F.col('distance_km').isNotNull(),'name_distance_pincode')
  .when(F.col('pin_agree'),'name_pincode').otherwise('name_distance'))
 pairs=pairs.withColumn('match_reason',F.when(F.col('pin_conflict'),'pincode conflict').when(F.col('geometry_conflict'),'coordinates conflict (distance > 2km)')
  .when(~F.col('eligible'),'insufficient name or geographic evidence')
  .when(F.col('match_status')=='ambiguous','competing candidate or close score')
  .otherwise('identity candidate only; never validates a service'))
 cand=pairs.select('facility_id','source','source_record_id','ext_name','match_score','name_sim','distance_km',
  'match_method','matched_fields','match_status','match_reason','rank_in_facility','rank_for_external','source_type','source_ref',
  F.when(F.col('match_status')=='accepted_high','high').when(F.col('match_status')=='accepted_medium','medium').otherwise('low').alias('confidence'))
 cand.write.mode('overwrite').option('overwriteSchema','true').saveAsTable('workspace.vf_silver.ext_facility_match_candidates')
 # One row per facility and source, including ambiguous/rejected best candidates. Unsafe matches never count as accepted.
 best=cand.filter('rank_in_facility=1')
 best=best.withColumn('match_status',F.when((F.col('match_status').startswith('accepted'))&(F.col('rank_for_external')>1),'ambiguous').otherwise(F.col('match_status')))
 best.select('facility_id','source','source_record_id','ext_name','match_score','name_sim','distance_km',
   'match_method','matched_fields','match_status','match_reason','source_type','source_ref','confidence')\
   .write.mode('overwrite').option('overwriteSchema','true').saveAsTable('workspace.vf_silver.ext_facility_matches')
 # Strict one-per-facility summary (safe LEFT JOIN for downstream Task C).
 accepted=best.filter(F.col('match_status').startswith('accepted'))
 accepted.groupBy('facility_id').agg(F.collect_list(F.struct('source','source_record_id','match_score','match_status','source_ref')).alias('external_matches'))\
   .write.mode('overwrite').option('overwriteSchema','true').saveAsTable('workspace.vf_silver.ext_facility_match_summary')
else: print('No ext_facilities_clean exists, skipped matcher.')

# COMMAND ----------
# Validation: print measured counts, duplicates, and provenance gaps (no fabricated results).
for tbl in ['workspace.vf_bronze.ext_cost_reference','workspace.vf_bronze.ext_wikidata_hospitals',
 'workspace.vf_silver.ext_source_status','workspace.vf_silver.ext_hmis_activity','workspace.vf_silver.ext_facilities_clean',
 'workspace.vf_silver.ext_facility_match_candidates','workspace.vf_silver.ext_facility_matches',
 'workspace.vf_silver.ext_facility_match_summary']:
 if spark.catalog.tableExists(tbl):
  print(tbl,spark.table(tbl).count())
if spark.catalog.tableExists('workspace.vf_silver.ext_facility_matches'):
 m=spark.table('workspace.vf_silver.ext_facility_matches')
 duplicates=m.groupBy('facility_id','source').count().filter('count > 1').count()
 print('duplicate facility-source matches:',duplicates)
 assert duplicates==0,'Match key duplication'
 print('match status breakdown:');m.groupBy('source','match_status').count().show(100,truncate=False)
 if spark.catalog.tableExists('workspace.vf_silver.ext_facility_match_summary'):
  d=spark.table('workspace.vf_silver.ext_facility_match_summary').groupBy('facility_id').count().filter('count>1').count()
  print('duplicate summary facilities:',d);assert d==0
if spark.catalog.tableExists('workspace.vf_silver.ext_facilities_clean'):
 print('missing external source references:',spark.table('workspace.vf_silver.ext_facilities_clean').filter('source_ref IS NULL OR source_type IS NULL OR confidence IS NULL').count())
print('Virtue Foundation facilities untouched:',spark.table('workspace.vf_silver.facilities').count())
# End: append "Implemented" to the live README ONLY after these validations have run and counts are recorded.
