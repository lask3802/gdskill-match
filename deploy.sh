#!/usr/bin/env bash
# Deploy GD Skill Match to GCP (Cloud Run serving + scheduled Cloud Function updater
# + GCS artifacts). Run while authenticated as the target account:
#   gcloud auth login
#   PROJECT_ID=your-project BILLING_ACCOUNT=XXXXXX-XXXXXX-XXXXXX ./deploy.sh
#
# Required env (no defaults — never hardcode account identifiers in a public repo):
set -euo pipefail

PROJECT_ID="${PROJECT_ID:?Set PROJECT_ID (e.g. gdskill-match)}"
BILLING_ACCOUNT="${BILLING_ACCOUNT:?Set BILLING_ACCOUNT (gcloud billing accounts list)}"
REGION="${REGION:-asia-east1}"
BUCKET="${BUCKET:-${PROJECT_ID}-artifacts}"
VERSION="${VERSION:-galaxywave_delta}"
SERVICE="${SERVICE:-gdskill}"
FUNCTION="${FUNCTION:-gdskill-updater}"
SCHED_JOB="${SCHED_JOB:-gdskill-daily}"
SCHEDULE="${SCHEDULE:-0 0 * * *}"          # daily; with --time-zone below
TIME_ZONE="${TIME_ZONE:-Asia/Taipei}"
MAX_INSTANCES="${MAX_INSTANCES:-2}"        # cap to bound abuse / runaway billing

echo "== project: $PROJECT_ID  region: $REGION  bucket: $BUCKET =="

# 1. project + billing
gcloud projects describe "$PROJECT_ID" >/dev/null 2>&1 \
  || gcloud projects create "$PROJECT_ID" --name="GD Skill Match"
gcloud config set project "$PROJECT_ID"
gcloud billing projects link "$PROJECT_ID" --billing-account="$BILLING_ACCOUNT"

# 2. APIs
gcloud services enable \
  run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  cloudfunctions.googleapis.com cloudscheduler.googleapis.com storage.googleapis.com \
  eventarc.googleapis.com iam.googleapis.com

PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
COMPUTE_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
UPDATER_SA="gdskill-updater@${PROJECT_ID}.iam.gserviceaccount.com"

# 3. dedicated least-privilege service account for the updater (write); the serving
#    Cloud Run only needs read.
gcloud iam service-accounts describe "$UPDATER_SA" >/dev/null 2>&1 \
  || gcloud iam service-accounts create gdskill-updater --display-name="GD Skill updater"

# 4. artifact bucket
gcloud storage buckets describe "gs://$BUCKET" >/dev/null 2>&1 \
  || gcloud storage buckets create "gs://$BUCKET" --location="$REGION"
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member="serviceAccount:${COMPUTE_SA}" --role="roles/storage.objectViewer"
gcloud storage buckets add-iam-policy-binding "gs://$BUCKET" \
  --member="serviceAccount:${UPDATER_SA}" --role="roles/storage.objectAdmin"

# 5. Cloud Run service (serving) — read-only artifacts, capped instances
gcloud run deploy "$SERVICE" --source . --region "$REGION" \
  --allow-unauthenticated --memory 1Gi --cpu 1 --timeout 300 --concurrency 40 \
  --max-instances "$MAX_INSTANCES" \
  --set-env-vars "GCS_BUCKET=${BUCKET},GD_VERSION=${VERSION}"
RUN_URL="$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')"

# 6. scheduled updater Cloud Function (2nd gen, Python), runs as the dedicated SA
gcloud functions deploy "$FUNCTION" --gen2 --runtime python311 --region "$REGION" \
  --source updater --entry-point update_data --trigger-http --no-allow-unauthenticated \
  --service-account "$UPDATER_SA" --memory 1Gi --timeout 540s \
  --set-env-vars "GCS_BUCKET=${BUCKET},GD_VERSION=${VERSION}"
FN_URL="$(gcloud functions describe "$FUNCTION" --region "$REGION" --gen2 --format='value(serviceConfig.uri)')"

# 7. let Cloud Scheduler invoke the (private) function via OIDC
gcloud run services add-iam-policy-binding "$FUNCTION" --region "$REGION" \
  --member="serviceAccount:${UPDATER_SA}" --role="roles/run.invoker"
gcloud scheduler jobs describe "$SCHED_JOB" --location "$REGION" >/dev/null 2>&1 \
  && gcloud scheduler jobs update http "$SCHED_JOB" --location "$REGION" \
       --schedule="$SCHEDULE" --time-zone="$TIME_ZONE" --uri="$FN_URL" --http-method=POST \
       --oidc-service-account-email="$UPDATER_SA" --oidc-token-audience="$FN_URL" \
  || gcloud scheduler jobs create http "$SCHED_JOB" --location "$REGION" \
       --schedule="$SCHEDULE" --time-zone="$TIME_ZONE" --uri="$FN_URL" --http-method=POST \
       --oidc-service-account-email="$UPDATER_SA" --oidc-token-audience="$FN_URL"

gcloud scheduler jobs run "$SCHED_JOB" --location "$REGION" || true

echo
echo "== DONE =="
echo "Cloud Run URL : $RUN_URL"
echo "Updater fn    : $FN_URL"
echo "Daily         : $SCHEDULE ($TIME_ZONE)"
