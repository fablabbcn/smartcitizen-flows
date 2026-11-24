# API

The REST API is served in `/api/v1/`. Reading the metadata is public, but writing requires an admin token for the Smart Citizen API.

| Endpoint | Content |
|---|---|
| `GET /blueprints`, `GET /blueprints/<name>[.json]` | Blueprints: the list (name, kind, url) and each one |
| `GET /hardware`, `GET /hardware/<name>[.json]` | Hardware: the same object in the list and by name (see below) |
| `GET /calibrations[?kind=alphasense_sensor\|afe_board]`, `GET /calibrations/<sensor_id>` | Calibrations of Alphasense sensors and AFE boards |
| `GET /names`, `GET /names/<name>` | Names that scdata and the blueprints give each Smart Citizen sensor id |
| `GET /parameters`, `GET /parameters/<type>` | Parameter sets of long processing |
| `PUT`, `DELETE /<blueprints\|hardware\|calibrations\|names\|parameters>/<name>` | Create, replace or delete (admin) |
| `GET /<kind>/<name>/revisions` | History of changes |
| `POST /hardware/<name>/check` | Checks a hardware without saving it |
| `GET /devices/health`, `GET /devices/<id>/health` | Health of the devices (admins, or researchers for their devices) |
| `GET /devices/<id>/series` | Results of long processing (`?channels=`, `?from=`, `?to=`, `?resample=1h`, `?format=csv`) |
| `GET /jobs`, `GET /runs/<id>`, `POST /jobs/sync` | Jobs and runs (admin) |

Note: The metadata used to be json files in [smartcitizen-data](https://github.com/fablabbcn/smartcitizen-data), which can still be imported and compared:

```
flask --app scflows metadata import <smartcitizen-data checkout> [--overwrite]
flask --app scflows metadata verify <smartcitizen-data checkout or url>
```

New sensors of the firmware and of the Smart Citizen API are added to the sensor names with `flask --app scflows names sync`.
