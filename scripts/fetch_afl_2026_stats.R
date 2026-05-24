#!/usr/bin/env Rscript
# fetch_afl_2026_stats.R
# ======================
# Fetches 2026 AFL player stats via fitzRoy (fryzigg primary, afltables fallback)
# and writes them to data/afl_2026_stats.csv.
#
# Called by .github/workflows/fetch-afl-2026.yml
#
# Why a separate script (not inline in the workflow):
#   Embedding R code inside a bash `Rscript -e "..."` double-quoted string causes
#   bash to interpret backtick-quoted R identifiers (e.g. `%||%`) as command
#   substitution, silently mangling the code and producing R parse errors such as
#   "unexpected assignment in ' <-'".  Using a file avoids all shell-quoting issues.

library(fitzRoy)
library(readr)
library(httr2)
library(dplyr)

output_path  <- "data/afl_2026_stats.csv"
cache_path   <- "data/cache/player_mapping_afltables.csv"
mapping_url  <- "https://github.com/jimmyday12/fitzRoy_data/raw/main/data-raw/afl_tables_playerstats/player_mapping_afltables.csv"
mapping_source      <- "unknown"
mapping_fetch_error <- NULL

dir.create(dirname(cache_path), showWarnings = FALSE, recursive = TRUE)

old_hash <- if (file.exists(output_path)) as.character(tools::md5sum(output_path)) else NA_character_

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

`%||%` <- function(x, y) if (is.null(x) || length(x) == 0 || is.na(x) || identical(x, "")) y else x

fetch_mapping_to_file <- function(dest, retries = 3) {
  for (attempt in seq_len(retries)) {
    cat(sprintf("Player mapping remote fetch attempt %d/%d ...\n", attempt, retries))
    ok <- tryCatch({
      resp <- request(mapping_url) |>
        req_timeout(30) |>
        req_error(is_error = function(resp) FALSE) |>
        req_perform()
      status <- resp_status(resp)
      if (status >= 200 && status < 300) {
        writeBin(resp_body_raw(resp), dest)
        TRUE
      } else {
        mapping_fetch_error <<- sprintf("HTTP %s", status)
        FALSE
      }
    }, error = function(e) {
      mapping_fetch_error <<- conditionMessage(e)
      FALSE
    })

    if (ok) return(TRUE)
    if (attempt < retries) Sys.sleep(min(2^(attempt - 1), 8))
  }
  FALSE
}

load_player_mapping <- function() {
  tmp_mapping <- tempfile(fileext = ".csv")
  remote_ok <- fetch_mapping_to_file(tmp_mapping, retries = 4)
  if (remote_ok) {
    file.copy(tmp_mapping, cache_path, overwrite = TRUE)
    mapping_source <<- "remote"
    cat("Player mapping loaded from: remote\n")
    return(suppressMessages(read_csv(tmp_mapping, show_col_types = FALSE)))
  }

  if (file.exists(cache_path)) {
    mapping_source <<- "cache"
    cat("Player mapping loaded from: cache\n")
    cat(sprintf("Player mapping remote fetch failed, using cache: %s\n",
                mapping_fetch_error %||% "unknown error"))
    return(suppressMessages(read_csv(cache_path, show_col_types = FALSE)))
  }

  stop(sprintf(
    "Failed to fetch player mapping from remote after retries (%s) and no local cache found at %s",
    mapping_fetch_error %||% "unknown error",
    cache_path
  ))
}

is_mapping_target <- function(file) {
  desc <- NULL
  if (inherits(file, "connection")) {
    desc <- tryCatch(summary(file)$description, error = function(e) NULL)
  } else if (is.character(file) && length(file) == 1) {
    desc <- file
  }
  is.character(desc) && grepl("player_mapping_afltables\\.csv$", desc)
}

# Patch read_csv so fitzRoy fetches the player mapping via our retry logic
# rather than failing silently when GitHub has rate-limited or is unavailable.
ns_readr <- asNamespace("readr")
original_read_csv <- get("read_csv", envir = ns_readr)
patched_read_csv <- function(file, ...) {
  if (is_mapping_target(file)) {
    return(load_player_mapping())
  }
  original_read_csv(file, ...)
}

unlockBinding("read_csv", ns_readr)
assign("read_csv", patched_read_csv, envir = ns_readr)
lockBinding("read_csv", ns_readr)
on.exit({
  unlockBinding("read_csv", ns_readr)
  assign("read_csv", original_read_csv, envir = ns_readr)
  lockBinding("read_csv", ns_readr)
}, add = TRUE)

# ---------------------------------------------------------------------------
# Fetch stats
# ---------------------------------------------------------------------------

data <- NULL

# Try fryzigg first so we preserve native match_id/player_id values that
# align with settlement joins in the app.
tryCatch({
  cat("Trying source=fryzigg ...\n")
  data <- fetch_player_stats(season = 2026, source = "fryzigg")
  if (!is.null(data) && nrow(data) > 0) {
    cat("fryzigg rows scraped:", nrow(data), "\n")
  } else {
    cat("fryzigg rows scraped: 0\n")
    data <- NULL
  }
}, error = function(e) {
  cat("fryzigg failed:", conditionMessage(e), "\n")
  data <<- NULL
})

# Fall back to afltables only if fryzigg returned nothing.
# WARNING: afltables output does not include native match_id values, so
# settlement may require natural-key fallback joins.
if (is.null(data) || nrow(data) == 0) {
  tryCatch({
    cat("Trying source=afltables ...\n")
    data <- fetch_player_stats(season = 2026, source = "afltables")
    if (!is.null(data) && nrow(data) > 0) {
      cat("afltables fallback rows:", nrow(data), "\n")
    } else {
      cat("afltables fallback rows: 0\n")
      data <- NULL
    }
  }, error = function(e) {
    cat("afltables failed:", conditionMessage(e), "\n")
    data <<- NULL
  })
}

if (is.null(data) || nrow(data) == 0) {
  stop(sprintf(
    "No rows returned from either afltables or fryzigg for season 2026 (player mapping source: %s)",
    mapping_source
  ))
}

# ---------------------------------------------------------------------------
# Write output — verify before overwriting
# ---------------------------------------------------------------------------

out_tmp <- tempfile(fileext = ".csv")
write_csv(data, out_tmp)
verify_rows <- nrow(suppressMessages(original_read_csv(out_tmp, show_col_types = FALSE)))
if (verify_rows == 0) {
  stop("Generated output file is empty; refusing to overwrite data/afl_2026_stats.csv")
}
file.copy(out_tmp, output_path, overwrite = TRUE)

new_hash    <- as.character(tools::md5sum(output_path))
file_updated <- !identical(old_hash, new_hash)

writeLines(as.character(verify_rows), "/tmp/afl_2026_stats_rows.txt")

cat("Rows written:", verify_rows, "\n")
cat("Cols written:", ncol(data), "\n")
cat("Player mapping source:", mapping_source, "\n")
cat("Final output row count:", verify_rows, "\n")
cat("Website data file updated:", ifelse(file_updated, "yes", "no"), "\n")
print(names(data))
