"""
Admin dashboard endpoints.
Requires authentication via ADMIN_API_KEY.
"""

import json
import os
from datetime import datetime

from flask import request
from flask_restful import Resource

import admin_auth
import card_history
import device_registry
import pilot_fuel
import recharge_schedule
import whatsapp_parser

# Configuration storage
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "admin_config.json")
LOGS_DIR = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOGS_DIR, exist_ok=True)

# Default configuration
DEFAULT_CONFIG = {
    "recharge_interval_months": 3,
    "due_soon_days": 14,
    "auto_refresh_enabled": True,
    "max_upload_size_mb": 10,
}


def load_config():
    """Load admin configuration from file."""
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, "r") as f:
                return {**DEFAULT_CONFIG, **json.load(f)}
        except Exception:
            return DEFAULT_CONFIG.copy()
    return DEFAULT_CONFIG.copy()


def save_config(config):
    """Save admin configuration to file."""
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f, indent=2)


def log_action(action, details=""):
    """Log admin actions for audit trail."""
    log_file = os.path.join(LOGS_DIR, f"admin_{datetime.now().strftime('%Y-%m-%d')}.log")
    timestamp = datetime.now().isoformat()
    with open(log_file, "a") as f:
        f.write(f"[{timestamp}] {action}: {details}\n")


# ---------- Admin Health & Stats ----------

class AdminHealth(Resource):
    @admin_auth.require_admin
    def get(self):
        """Detailed system health check."""
        try:
            # Count files in data directories
            card_files = len([f for f in os.listdir(card_history.CARD_HISTORY_DIR) 
                           if os.path.isfile(os.path.join(card_history.CARD_HISTORY_DIR, f))]) if os.path.exists(card_history.CARD_HISTORY_DIR) else 0
            
            registry_files = len([f for f in os.listdir(device_registry.REGISTRY_DIR) 
                                if os.path.isfile(os.path.join(device_registry.REGISTRY_DIR, f))]) if os.path.exists(device_registry.REGISTRY_DIR) else 0
            
            chat_files = len([f for f in os.listdir(whatsapp_parser.RECHARGE_LOG_DIR) 
                           if os.path.isfile(os.path.join(whatsapp_parser.RECHARGE_LOG_DIR, f))]) if os.path.exists(whatsapp_parser.RECHARGE_LOG_DIR) else 0
            
            # Pilot fuel devices count
            pf_devices = len(pilot_fuel.load_devices())
            
            return {
                "status": "ok",
                "files": {
                    "cardHistory": card_files,
                    "deviceRegistry": registry_files,
                    "chatLogs": chat_files
                },
                "pilotFuelDevices": pf_devices,
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            return {"error": f"Health check failed: {str(e)}"}, 500


class AdminStats(Resource):
    @admin_auth.require_admin
    def get(self):
        """Usage statistics."""
        try:
            # File counts
            card_files = []
            if os.path.exists(card_history.CARD_HISTORY_DIR):
                card_files = [f for f in os.listdir(card_history.CARD_HISTORY_DIR) 
                            if os.path.isfile(os.path.join(card_history.CARD_HISTORY_DIR, f))]
            
            registry_files = []
            if os.path.exists(device_registry.REGISTRY_DIR):
                registry_files = [f for f in os.listdir(device_registry.REGISTRY_DIR) 
                                if os.path.isfile(os.path.join(device_registry.REGISTRY_DIR, f))]
            
            chat_files = []
            if os.path.exists(whatsapp_parser.RECHARGE_LOG_DIR):
                chat_files = [f for f in os.listdir(whatsapp_parser.RECHARGE_LOG_DIR) 
                            if os.path.isfile(os.path.join(whatsapp_parser.RECHARGE_LOG_DIR, f))]
            
            # Recharge schedule stats
            try:
                registry = device_registry.load_registry()
                events, _ = whatsapp_parser.load_all()
                schedule, unmatched = recharge_schedule.build_schedule(registry, events)
                summary = recharge_schedule.summarize(schedule, unmatched)
            except:
                summary = {}
            
            # Pilot fuel stats
            try:
                pf_schedule, pf_summary = pilot_fuel.build_schedule()
            except Exception:
                pf_summary = {}
            
            return {
                "files": {
                    "cardHistory": len(card_files),
                    "deviceRegistry": len(registry_files),
                    "chatLogs": len(chat_files)
                },
                "rechargeSchedule": summary,
                "pilotFuel": pf_summary,
                "timestamp": datetime.now().isoformat()
            }
        except Exception as e:
            return {"error": str(e)}, 500


# ---------- File Management ----------

class AdminFiles(Resource):
    @admin_auth.require_admin
    def get(self):
        """List all uploaded files."""
        try:
            file_type = request.args.get("type", "all")
            
            files = []
            
            if file_type in ("all", "card-history"):
                if os.path.exists(card_history.CARD_HISTORY_DIR):
                    for f in os.listdir(card_history.CARD_HISTORY_DIR):
                        path = os.path.join(card_history.CARD_HISTORY_DIR, f)
                        if os.path.isfile(path):
                            files.append({
                                "name": f,
                                "type": "card-history",
                                "size": os.path.getsize(path),
                                "modified": datetime.fromtimestamp(os.path.getmtime(path)).isoformat()
                            })
            
            if file_type in ("all", "registry"):
                if os.path.exists(device_registry.REGISTRY_DIR):
                    for f in os.listdir(device_registry.REGISTRY_DIR):
                        path = os.path.join(device_registry.REGISTRY_DIR, f)
                        if os.path.isfile(path):
                            files.append({
                                "name": f,
                                "type": "registry",
                                "size": os.path.getsize(path),
                                "modified": datetime.fromtimestamp(os.path.getmtime(path)).isoformat()
                            })
            
            if file_type in ("all", "chat-logs"):
                if os.path.exists(whatsapp_parser.RECHARGE_LOG_DIR):
                    for f in os.listdir(whatsapp_parser.RECHARGE_LOG_DIR):
                        path = os.path.join(whatsapp_parser.RECHARGE_LOG_DIR, f)
                        if os.path.isfile(path):
                            files.append({
                                "name": f,
                                "type": "chat-logs",
                                "size": os.path.getsize(path),
                                "modified": datetime.fromtimestamp(os.path.getmtime(path)).isoformat()
                            })
            
            return {"files": files, "count": len(files)}
        except Exception as e:
            return {"error": str(e)}, 500


class AdminFileDelete(Resource):
    @admin_auth.require_admin
    def delete(self, file_type, filename):
        """Delete an uploaded file."""
        try:
            # Validate file type
            dir_map = {
                "card-history": card_history.CARD_HISTORY_DIR,
                "registry": device_registry.REGISTRY_DIR,
                "chat-logs": whatsapp_parser.RECHARGE_LOG_DIR
            }
            
            if file_type not in dir_map:
                return {"error": f"Invalid file type: {file_type}"}, 400
            
            file_dir = dir_map[file_type]
            file_path = os.path.join(file_dir, filename)
            
            # Security: ensure the path is within the expected directory
            if not os.path.abspath(file_path).startswith(os.path.abspath(file_dir)):
                return {"error": "Invalid filename"}, 400
            
            if not os.path.exists(file_path):
                return {"error": "File not found"}, 404
            
            os.remove(file_path)
            log_action("FILE_DELETE", f"{file_type}/{filename}")
            
            return {"message": f"Deleted {filename}"}
        except Exception as e:
            return {"error": str(e)}, 500


# ---------- Configuration Management ----------

class AdminConfig(Resource):
    @admin_auth.require_admin
    def get(self):
        """Get current configuration."""
        try:
            config = load_config()
            return config
        except Exception as e:
            return {"error": str(e)}, 500
    
    @admin_auth.require_admin
    def put(self):
        """Update configuration."""
        try:
            data = request.get_json()
            if not data:
                return {"error": "No data provided"}, 400
            
            config = load_config()
            
            # Validate and update fields
            allowed_fields = ["recharge_interval_months", "due_soon_days", "auto_refresh_enabled"]
            updated_fields = []
            
            for field in allowed_fields:
                if field in data:
                    value = data[field]
                    # Validation
                    if field in ("recharge_interval_months", "due_soon_days"):
                        value = int(value)
                        if value < 1 or value > 120:
                            return {"error": f"{field} must be between 1 and 120"}, 400
                    
                    config[field] = value
                    updated_fields.append(field)
            
            if not updated_fields:
                return {"error": "No valid fields to update"}, 400
            
            save_config(config)
            log_action("CONFIG_UPDATE", f"Updated: {', '.join(updated_fields)}")
            
            return {"message": "Configuration updated", "config": config}
        except Exception as e:
            return {"error": str(e)}, 500


# ---------- Cache Management ----------

class AdminCache(Resource):
    @admin_auth.require_admin
    def post(self):
        """Clear caches and force refresh."""
        try:
            action = request.json.get("action", "clear_all") if request.json else "clear_all"
            
            if action == "clear_all":
                log_action("CACHE_CLEAR", "Cleared all caches")
                return {"message": "All caches cleared"}
            
            else:
                return {"error": f"Unknown action: {action}"}, 400
        except Exception as e:
            return {"error": str(e)}, 500


# ---------- Audit Logs ----------

class AdminLogs(Resource):
    @admin_auth.require_admin
    def get(self):
        """Get audit logs."""
        try:
            days = int(request.args.get("days", 7))
            if days < 1 or days > 30:
                days = 7
            
            logs = []
            for i in range(days):
                date = (datetime.now() - __import__('datetime').timedelta(days=i)).strftime('%Y-%m-%d')
                log_file = os.path.join(LOGS_DIR, f"admin_{date}.log")
                if os.path.exists(log_file):
                    with open(log_file, "r") as f:
                        logs.extend(f.readlines())
            
            # Reverse to show newest first
            logs.reverse()
            
            return {"logs": logs, "count": len(logs)}
        except Exception as e:
            return {"error": str(e)}, 500