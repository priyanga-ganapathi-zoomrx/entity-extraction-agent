@Library('congress-ci-cd@congress-temporal-chart-changes') _

ferma_congress_dev.call(
  repo_url: "https://github.com/ZoomRx/entity-extraction-agent.git",
  deployment_type: "backend",
  application_name: "NA",
  chart_version: "NA",
  mode_parameters: "build",
  dockerfile_dir: "deploy/entity-extraction-agent/",
  chart_name: "NA",
  image_name: "entity-extraction-agent"
)
