# API Examples

## Health

```powershell
Invoke-RestMethod http://127.0.0.1:8000/health |
ConvertTo-Json -Depth 10
```

## Create Task

```powershell
$body = @{
  task = "Read local docs, query database metrics, retrieve trace evidence, and generate a markdown report"
  report_type = "summary"
  source_mode = "mock"
  allowed_tools = @("file_reader", "sql_query", "rag_search", "report_writer")
} | ConvertTo-Json -Depth 10

$task = Invoke-RestMethod `
  -Uri http://127.0.0.1:8000/api/tasks `
  -Method POST `
  -ContentType "application/json" `
  -Body $body
```

## Inspect Plan

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/tasks/$($task.run_id)/plan" |
ConvertTo-Json -Depth 20
```

## Run Task

```powershell
Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/api/tasks/$($task.run_id)/run" `
  -Method POST `
  -ContentType "application/json" `
  -Body '{}' |
ConvertTo-Json -Depth 20
```

## Inspect Trace

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/tasks/$($task.run_id)/trace" |
ConvertTo-Json -Depth 20
```

## Read Report

```powershell
Invoke-RestMethod "http://127.0.0.1:8000/api/reports/$($task.run_id)" |
ConvertTo-Json -Depth 20
```

## Confirm HITL

```powershell
$confirmBody = @{
  approved = $true
  comment = "Approved for demo."
  resume = $true
} | ConvertTo-Json -Depth 10

Invoke-RestMethod `
  -Uri "http://127.0.0.1:8000/api/tasks/$($task.run_id)/confirm" `
  -Method POST `
  -ContentType "application/json" `
  -Body $confirmBody |
ConvertTo-Json -Depth 20
```

## Direct GitHub Mock Tool

```powershell
Invoke-RestMethod `
  -Uri http://127.0.0.1:8000/api/tools/mcp_github_search/execute `
  -Method POST `
  -ContentType "application/json" `
  -Body '{"arguments":{"query":"traceable research agent tool registry","repo":"piao666/traceable-research-agent","limit":3,"mode":"mock"}}' |
ConvertTo-Json -Depth 20
```
