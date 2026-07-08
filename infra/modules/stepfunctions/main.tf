locals {
  prefix = "investment-${var.environment}"
}

resource "aws_sfn_state_machine" "pipeline" {
  name     = "${local.prefix}-pipeline"
  role_arn = var.sfn_role_arn

  definition = jsonencode({
    Comment = "Investment pipeline: ingest → screen-score → explain → publish"
    StartAt = "Ingest"
    States = {
      Ingest = {
        Type     = "Task"
        Resource = var.function_arns["ingest"]
        Retry = [{
          ErrorEquals     = ["States.TaskFailed"]
          IntervalSeconds = 30
          MaxAttempts     = 3
          BackoffRate     = 2
        }]
        Catch = [{
          ErrorEquals = ["States.ALL"]
          Next        = "NotifyError"
          ResultPath  = "$.error"
        }]
        Next = "ScreenScore"
      }
      ScreenScore = {
        Type     = "Task"
        Resource = var.function_arns["screen_score"]
        Retry = [{
          ErrorEquals     = ["States.TaskFailed"]
          IntervalSeconds = 10
          MaxAttempts     = 2
          BackoffRate     = 2
        }]
        Catch = [{
          ErrorEquals = ["States.ALL"]
          Next        = "NotifyError"
          ResultPath  = "$.error"
        }]
        Next = "Explain"
      }
      Explain = {
        Type       = "Task"
        Resource   = var.function_arns["explain"]
        ResultPath = "$.explain_result"
        Retry = [{
          ErrorEquals     = ["States.TaskFailed"]
          IntervalSeconds = 30
          MaxAttempts     = 3
          BackoffRate     = 2
        }]
        Catch = [{
          ErrorEquals = ["States.ALL"]
          Next        = "NotifyError"
          ResultPath  = "$.error"
        }]
        Next = "Publish"
      }
      Publish = {
        Type     = "Task"
        Resource = var.function_arns["publish"]
        Parameters = {
          "run_id.$"     = "$.run_id"
          "report_key.$" = "$.explain_result.report_key"
        }
        Retry = [{
          ErrorEquals     = ["States.TaskFailed"]
          IntervalSeconds = 10
          MaxAttempts     = 2
          BackoffRate     = 2
        }]
        Catch = [{
          ErrorEquals = ["States.ALL"]
          Next        = "NotifyError"
          ResultPath  = "$.error"
        }]
        End = true
      }
      NotifyError = {
        Type     = "Task"
        Resource = "arn:aws:states:::sns:publish"
        Parameters = {
          TopicArn = var.sns_topic_arn
          Message = {
            "Input.$" = "$.error"
          }
          Subject = "[InvestmentSystem] パイプラインエラー"
        }
        Next = "PipelineFailed"
      }
      PipelineFailed = {
        Type  = "Fail"
        Error = "PipelineFailed"
        Cause = "パイプラインでエラーが発生しました。SNS通知を確認してください。"
      }
    }
  })
}
