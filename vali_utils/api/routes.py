# vali_utils/api/routes.py

from fastapi import APIRouter, HTTPException, Depends
import bittensor as bt
import datetime as dt
from typing import Optional
from common.data import DataSource
from common.protocol import OnDemandRequest
from common import utils  # Import your utils
from .models import QueryRequest, QueryResponse, HealthResponse, MinerInfo, LabelSize, AgeSize
from typing import List
import random

router = APIRouter()


def get_validator():
    """Dependency to get validator instance"""
    if not hasattr(get_validator, 'api'):
        raise HTTPException(503, "API server not initialized")
    return get_validator.api.validator


@router.post("/query", response_model=QueryResponse)
async def query_data(request: QueryRequest, validator=Depends(get_validator)):
    """Handle data queries"""
    try:
        # Get miner UIDs using your utility function
        # This excludes validators and the current validator's UID
        miner_uids = utils.get_miner_uids(validator.metagraph, validator.uid)

        # Sort by stake and get top 50%
        miners = sorted(
            miner_uids,
            key=lambda uid: validator.metagraph.I[uid],
            reverse=True
        )[:len(miner_uids) // 2]

        if not miners:
            raise HTTPException(503, "No miners available")

        # Create request synapse
        synapse = OnDemandRequest(
            source=DataSource[request.source.upper()],
            usernames=request.usernames,
            keywords=request.keywords,
            start_date=request.start_date,
            end_date=request.end_date,
            limit=request.limit
        )

        # Query miners in parallel
        responses = []
        async with bt.dendrite(wallet=validator.wallet) as dendrite:
            uid = random.choice(miner_uids)
            # Check if miner is qualified using your utility
            if utils.is_miner(uid, validator.metagraph):
                axon = validator.metagraph.axons[uid]
                try:
                    response = await dendrite.forward(
                        axons=[axon],
                        synapse=synapse,
                        timeout=30
                    )
                    if response and response.data:
                        responses.append(response.data)
                except Exception as e:
                    bt.logging.error(f"Error querying miner {uid}: {str(e)}")


        # TODO ADD VALIDATION

        # Process data
        all_data = []
        seen = set()
        for response in responses:
            for item in response:
                item_hash = hash(str(item))
                if item_hash not in seen:
                    seen.add(item_hash)
                    all_data.append(item)

        return {
            "status": "success",
            "data": all_data[:request.limit],
            "meta": {
                "total_responses": len(responses),
                "unique_items": len(all_data),
                "miners_queried": len(miners),
                "total_miners": len(miner_uids)
            }
        }

    except Exception as e:
        bt.logging.error(f"Error processing request: {str(e)}")
        raise HTTPException(500, str(e))


@router.get("/health", response_model=HealthResponse)
async def health_check(validator=Depends(get_validator)):
    """Health check endpoint"""
    miner_uids = utils.get_miner_uids(validator.metagraph, validator.uid)
    return {
        "status": "healthy" if validator.is_healthy() else "unhealthy",
        "timestamp": dt.datetime.utcnow(),
        "miners_available": len(miner_uids)
    }


@router.get("/labels/{source}", response_model=List[LabelSize])
async def get_label_sizes(
    source: str,
    validator=Depends(get_validator)
):
    """Get content size information by label for a specific source"""
    try:
        # Validate source
        try:
            source_id = DataSource[source.upper()].value
        except KeyError:
            raise HTTPException(400, f"Invalid source: {source}")
            
        with validator.evaluator.storage.lock:
            connection = validator.evaluator.storage._create_connection()
            cursor = connection.cursor()
            
            cursor.execute("""
                SELECT 
                    labelValue,
                    contentSizeBytes,
                    adjContentSizeBytes
                FROM APILabelSize
                WHERE source = ?
                ORDER BY adjContentSizeBytes DESC
            """, [source_id])
            
            labels = [
                LabelSize(
                    label_value=row[0],
                    content_size_bytes=row[1],
                    adj_content_size_bytes=row[2]
                )
                for row in cursor.fetchall()
            ]
            
            connection.close()
            return labels
            
    except Exception as e:
        bt.logging.error(f"Error getting label sizes: {str(e)}")
        raise HTTPException(500, str(e))


@router.get("/ages/{source}", response_model=List[AgeSize])
async def get_age_sizes(
    source: str,
    validator=Depends(get_validator)
):
    """Get content size information by age bucket for a specific source from Miner and MinerIndex validator tables"""
    try:
        # Validate source
        try:
            source_id = DataSource[source.upper()].value
        except KeyError:
            raise HTTPException(400, f"Invalid source: {source}")

        with validator.evaluator.storage.lock:
            connection = validator.evaluator.storage._create_connection()
            cursor = connection.cursor()
            
            cursor.execute("""
                SELECT 
                    timeBucketId,
                    SUM(contentSizeBytes) as contentSizeBytes,
                    SUM(contentSizeBytes * credibility) as adjContentSizeBytes
                FROM Miner
                JOIN MinerIndex USING (minerId)
                WHERE source = ?
                GROUP BY timeBucketId
                ORDER BY timeBucketId DESC
            """, [source_id])

            ages = [
                AgeSize(
                    time_bucket_id=row[0],
                    content_size_bytes=row[1],
                    adj_content_size_bytes=row[2]
                )
                for row in cursor.fetchall()
            ]
            connection.close()
            return ages
    except Exception as e:
        raise HTTPException(500, f"Error retrieving age sizes: {str(e)}")