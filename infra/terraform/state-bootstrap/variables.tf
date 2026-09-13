variable "location" {
  description = "Azure region for the remote-state storage account."
  type        = string
}

variable "unique_suffix" {
  description = "Lowercase suffix making the storage account globally unique."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]{4,12}$", var.unique_suffix))
    error_message = "unique_suffix must contain 4-12 lowercase letters or digits."
  }
}

variable "state_principal_id" {
  description = "Object ID of the human or workload identity authorized to access state blobs."
  type        = string

  validation {
    condition     = can(regex("^[0-9a-fA-F-]{36}$", var.state_principal_id))
    error_message = "state_principal_id must be an Entra object ID."
  }
}
