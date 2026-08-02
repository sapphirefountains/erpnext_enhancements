# ============================================================================
# TRAINING MEDIA BUCKET
# ============================================================================
# Private bucket the Training module streams course video from, plus the
# narrow service account that signs URLs for it.
#
# WHY THIS EXISTS AT ALL. Authors upload training video to Google Drive, and it
# would be far simpler to embed it straight from there. It does not work: a
# Drive preview frame is cross-origin, so the player cannot read currentTime,
# cannot pause(), and cannot observe seeking. That kills in-video checkpoints
# AND watch-coverage measurement — two of the three ways the module checks
# whether somebody actually engaged with a lesson.
#
# So on publish the video is copied here once, and the player is handed a
# short-lived V4 signed URL it can play in a real <video> element. Same-origin
# is not required; CORS-clean byte-range serving is, and that is what this
# gives us. It also means a customer with no Google account can watch, which a
# Drive link cannot do without making the file world-readable.
#
# Applied against prod: terraform apply -var-file=prod.tfvars
# ============================================================================

resource "google_storage_bucket" "training_media" {
  count    = var.enable_training_media_bucket ? 1 : 0
  name     = var.training_media_bucket_name
  project  = module.project.project_id
  location = var.training_media_bucket_location

  storage_class = "STANDARD"

  # Uniform access + enforced public-access prevention together make it
  # *impossible* to accidentally publish a training video by setting an object
  # ACL. Training content includes safety and chemical-handling material; a
  # world-readable bucket is not a mistake worth leaving available.
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  # Deliberately NOT force_destroy. Completion records cite the exact video a
  # learner watched, so an accidental `terraform destroy` must not silently
  # take the evidence with it.
  force_destroy = false

  # Object versioning is off on purpose. Course *content* versioning is handled
  # in the application by Training Course Version, and each published version
  # writes its own immutable object key. A second, storage-level notion of
  # "version" would only be something to confuse it with later.
  versioning {
    enabled = false
  }

  # No lifecycle deletion rule. A training library is tens of GB and storage is
  # pennies per GB/month; keeping every version indefinitely is what lets an
  # audit answer "which video did this person actually watch, in 2026".

  # The player sets crossorigin="anonymous" so it can read frame data and so
  # media errors surface properly instead of as an opaque failure. Without this
  # rule the browser blocks the request outright.
  cors {
    origin          = var.training_media_cors_origins
    method          = ["GET", "HEAD"]
    response_header = ["Content-Type", "Content-Length", "Content-Range", "Accept-Ranges", "Range"]
    max_age_seconds = 3600
  }

  labels = {
    app       = "erpnext-enhancements"
    component = "training-media"
  }
}

# ----------------------------------------------------------------------------
# The signing identity
# ----------------------------------------------------------------------------
# A dedicated account rather than reusing the Drive service account, which
# holds full Drive scope — widening an already-broad credential is worse than
# adding a narrow one. This account can read and write objects in exactly one
# bucket and can reach nothing else in the project.
#
# Its JSON key is created by hand (`gcloud iam service-accounts keys create`)
# and pasted into Training Settings, which stores it in a Password field. The
# key is deliberately NOT managed by Terraform: a google_service_account_key
# resource would put the private key in plaintext in the state file, and that
# state lives in a bucket several people can read.
resource "google_service_account" "training_media" {
  count        = var.enable_training_media_bucket ? 1 : 0
  project      = module.project.project_id
  account_id   = "sa-training-media"
  display_name = "Training media signer"
  description  = "Signs short-lived URLs for training course video and copies it in from Drive. Scoped to the training media bucket only."
}

# objectAdmin, not objectViewer: the same identity both reads (to sign playback
# URLs) and writes (the publish-time copy from Drive). Granted on the bucket,
# never at project level.
resource "google_storage_bucket_iam_member" "training_media_object_admin" {
  count  = var.enable_training_media_bucket ? 1 : 0
  bucket = google_storage_bucket.training_media[0].name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.training_media[0].email}"
}

output "training_media_bucket" {
  description = "Name of the training media bucket — paste into Training Settings > GCS Video Bucket."
  value       = var.enable_training_media_bucket ? google_storage_bucket.training_media[0].name : null
}

output "training_media_service_account" {
  description = "Service account to create a JSON key for (step 2 of the runbook)."
  value       = var.enable_training_media_bucket ? google_service_account.training_media[0].email : null
}
