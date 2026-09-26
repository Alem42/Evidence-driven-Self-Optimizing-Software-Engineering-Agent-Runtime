# Multi-Agent Software Engineering Assistant

## 可扩展 Agent 系统架构设计与项目规划

## 1. 项目定位

### 项目名称

Multi-Agent Software Engineering Assistant (MASA)

### 项目目标

构建一个面向软件开发流程的多 Agent 协作系统，让多个具备不同职责的 AI
Agent 协同完成：

-   需求分析
-   系统设计
-   代码生成
-   代码修改
-   自动测试
-   Code Review
-   Bug 修复
-   项目文档生成

项目定位不是简单的代码生成助手，而是一个：

> 面向软件工程生命周期的 Agent Orchestration Platform。

核心体现：

-   Agent Runtime 设计能力
-   Multi-Agent 协作能力
-   Tool Calling 能力
-   Workflow 编排能力
-   长任务状态管理能力
-   软件工程自动化能力

------------------------------------------------------------------------

# 2. 总体架构设计

## 2.1 高层架构

                        User / IDE Plugin

                              |
                              |

                        API Gateway

                              |

                  Agent Runtime Platform

                              |

            +--------------------------------+

            |        Workflow Engine         |

            |                                |

            | Task Planning                  |

            | Agent Scheduling               |

            | State Management               |

            | Retry / Recovery               |

            +--------------------------------+

                              |

           ------------------------------------------------

           |              |              |                |

     Architect      Developer       Tester          Reviewer
     Agent          Agent           Agent           Agent


           |              |              |                |

           ------------------------------------------------

                              |

                        Tool Layer

                              |

     ------------------------------------------------------

     |              |              |             |

    Code Search   File System   Terminal     Git Tool

    Compiler      Test Runner   Browser      Database


                              |

                        Project Repository

------------------------------------------------------------------------

# 3. 核心设计思想

## 3.1 Agent Runtime 自研

不直接依赖高级 Agent 框架。

实现自己的 Runtime Layer。

核心模块：

    agent-runtime/

    ├── core
    │
    ├── planner
    │
    ├── executor
    │
    ├── memory
    │
    ├── workflow
    │
    ├── tool
    │
    ├── trace
    │
    └── evaluation

------------------------------------------------------------------------

# 4. Agent Runtime 详细设计

## 4.1 State Management

负责维护 Agent 生命周期。

数据结构：

``` json
{
 "task_id":"",
 "goal":"",
 "current_agent":"",
 "messages":[],
 "tool_history":[],
 "context":{},
 "status":"running"
}
```

支持：

-   checkpoint
-   resume
-   rollback

对应企业需求：

-   Context Management
-   Memory

------------------------------------------------------------------------

# 4.2 Planning Engine

负责任务拆解。

输入：

    实现用户登录模块

输出：

    Task 1:
    数据库设计

    Task 2:
    Backend API

    Task 3:
    Frontend

    Task 4:
    Testing

支持：

-   Plan Generation
-   Plan Revision
-   Dynamic Planning

------------------------------------------------------------------------

# 4.3 Agent Scheduler

负责 Agent 调度。

例如：

    Planner

     ↓

    Architect Agent

     ↓

    Developer Agent

     ↓

    Tester Agent

     ↓

    Reviewer Agent

支持：

-   Sequential Workflow
-   Parallel Execution
-   Dependency Management

------------------------------------------------------------------------

# 4.4 Tool Registry

设计类似 MCP Tool。

接口：

``` python
class Tool:

    name

    description

    input_schema

    execute()
```

工具：

    Code Search Tool

    File Edit Tool

    Git Tool

    Terminal Tool

    Test Tool

    Browser Tool

    Database Tool

未来可以扩展：

-   MCP Server
-   External API

------------------------------------------------------------------------

# 4.5 Execution Loop

核心循环：

    Observe

       |

    Think

       |

    Plan

       |

    Act

       |

    Tool Result

       |

    Update State

       |

    Next Step

支持：

-   最大执行步数限制
-   Tool timeout
-   Exception handling
-   Retry

------------------------------------------------------------------------

# 5. Multi-Agent 设计

## 5.1 Manager Agent

职责：

项目经理。

功能：

-   接收用户需求
-   创建任务计划
-   分配 Agent

------------------------------------------------------------------------

## 5.2 Architect Agent

职责：

系统设计。

输出：

-   Architecture Diagram
-   API Design
-   Database Schema

------------------------------------------------------------------------

## 5.3 Developer Agent

职责：

代码实现。

能力：

-   阅读代码
-   修改文件
-   创建 Pull Request

工具：

-   File Tool
-   Git Tool

------------------------------------------------------------------------

## 5.4 Tester Agent

职责：

自动测试。

能力：

-   生成测试代码
-   执行测试
-   分析失败原因

------------------------------------------------------------------------

## 5.5 Reviewer Agent

职责：

代码审查。

检查：

-   Bug
-   Security
-   Performance
-   Code Style

------------------------------------------------------------------------

# 6. Go 工程架构设计

## 为什么使用 Go

Go 负责：

-   Runtime Server
-   Agent Scheduling
-   Workflow Execution
-   Service Communication

Python负责：

-   LLM调用
-   Prompt
-   Agent Logic

架构：

                     Go Runtime Server

                             |

                  ------------------

                  |                |

              Scheduler        Workflow


                  |

            gRPC Communication


                  |

              Python LLM Worker

------------------------------------------------------------------------

# 7. 后端技术栈

## Runtime

Go

Gin

gRPC

## Storage

PostgreSQL

Redis

## Message Queue

Kafka / RabbitMQ

## Container

Docker

Docker Compose

## AI

Python

LLM API

Embedding

## Frontend

React

------------------------------------------------------------------------

# 8. 数据流设计

用户提交任务：

    Request

     ↓

    Task Manager

     ↓

    Planner

     ↓

    Workflow DAG

     ↓

    Agent Scheduler

     ↓

    Agent Execution

     ↓

    Tool Calling

     ↓

    Result

     ↓

    Evaluation

     ↓

    Final Response

------------------------------------------------------------------------

# 9. 可观测性系统

记录：

## Agent Trace

    Agent:

    Architect

    Input:

    Output:

    Tool:

    Latency:

    Token:

## Metrics

-   Task Success Rate
-   Tool Success Rate
-   Average Latency
-   Token Cost
-   Retry Count

------------------------------------------------------------------------

# 10. Evaluation System

评价：

## Agent Level

-   Task Completion Rate
-   Planning Accuracy

## Tool Level

-   Tool Selection Accuracy
-   Execution Success Rate

## Code Level

-   Unit Test Pass Rate
-   Code Quality

------------------------------------------------------------------------

# 11. MVP 开发路线

## Phase 1 (3 days)

实现：

-   Agent Runtime
-   State
-   Tool Registry
-   Execution Loop

目标：

单 Agent 完成任务。

------------------------------------------------------------------------

## Phase 2 (5 days)

加入：

-   Manager Agent
-   Developer Agent
-   Tester Agent

目标：

Multi-Agent Workflow。

------------------------------------------------------------------------

## Phase 3 (1 week)

加入：

-   Go Runtime
-   gRPC
-   Redis
-   Queue

目标：

工程化。

------------------------------------------------------------------------

## Phase 4

加入：

-   IDE Plugin
-   MCP
-   Cloud Deployment

------------------------------------------------------------------------

# 12. 最终简历描述

英文：

> Designed and implemented a scalable multi-agent software engineering
> platform with a custom Agent Runtime, supporting planning, workflow
> orchestration, tool execution, memory management and agent
> collaboration. Built Go-based runtime services and integrated coding,
> testing and review agents to automate software development workflows.

中文：

> 设计并实现可扩展 Multi-Agent 软件工程平台，自研 Agent
> Runtime，实现任务规划、Workflow 编排、Tool 调用、状态管理和多 Agent
> 协作；基于 Go 构建 Runtime
> 服务，支持代码生成、自动测试和代码审查等软件开发流程自动化。

------------------------------------------------------------------------

# 13. 后续扩展方向

未来可以继续扩展：

-   MCP Tool Ecosystem
-   IDE Agent
-   Autonomous Debugging
-   Code Repository Understanding
-   Agent Marketplace
-   Enterprise Workflow Agent

该项目最终目标：

从 Demo Agent

升级为：

Agent Infrastructure Platform。
