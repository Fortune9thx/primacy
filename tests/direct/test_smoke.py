from conftest import CONTRACT_PATH, TREASURY_HEX


def test_deploy_and_read_constitution(direct_deploy):
    contract = direct_deploy(CONTRACT_PATH, TREASURY_HEX)
    constitution = contract.get_constitution()
    assert constitution["lanes"]["MAJORS"] == ["BTC", "ETH", "SOL"]
    assert constitution["fee_bps"] == 200
    assert contract.get_config()["treasury"].lower() == TREASURY_HEX.lower()
