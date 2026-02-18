@Library('congress-ci-cd@congress-temporal-chart-changes') _

ferma_congress_dev.call(
  repo_url: "https://github.com/ZoomRx/entity-extraction-agent.git",
  deployment_type: "backend",
  application_name: "entity-extraction-agent-indication-extraction-worker",
  chart_version: "v3.1.0",
  mode_parameters: "deploy,rollback,restart",
  dockerfile_dir: "deploy/entity-extraction-agent/",
  chart_name: "application_chart",
  image_name: "entity-extraction-agent"
)
