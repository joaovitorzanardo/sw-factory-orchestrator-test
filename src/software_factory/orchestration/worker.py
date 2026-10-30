import asyncio
import logging

from dotenv import load_dotenv
from temporalio.client import Client
from temporalio.worker import Worker

from software_factory.config import FactorySettings
from software_factory.orchestration.activities import SandboxActivities
from software_factory.orchestration.workflow import PlanIssueWorkflow


async def run_worker(settings: FactorySettings) -> None:
    client = await Client.connect(settings.temporal_address)
    activities = SandboxActivities(settings)
    worker = Worker(
        client,
        task_queue=settings.task_queue,
        workflows=[PlanIssueWorkflow],
        activities=[
            activities.create_sandbox,
            activities.clone_repo,
            activities.generate_plan,
            activities.kill_sandbox,
        ],
    )
    logging.getLogger(__name__).info("worker ouvindo task queue %s", settings.task_queue)
    await worker.run()


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO)
    asyncio.run(run_worker(FactorySettings.from_env()))


if __name__ == "__main__":
    main()
