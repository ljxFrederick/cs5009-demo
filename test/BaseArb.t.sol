// SPDX-License-Identifier: MIT
pragma solidity ^0.8.24;

import "forge-std/Test.sol";

interface IERC20 {
    function balanceOf(address) external view returns (uint256);
    function transfer(address,uint256) external returns (bool);
}
interface IV3Pool {
    function swap(address recipient,bool zeroForOne,int256 amountSpecified,uint160 sqrtPriceLimitX96,bytes calldata data)
        external returns (int256 amount0,int256 amount1);
}

contract AtomicTwoPoolArb {
    address constant USDC = 0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;
    address constant WETH = 0x4200000000000000000000000000000000000006;
    address constant PAN  = 0x72AB388E2E2F6FaceF59E3C3FA2C4E29011c2D38;
    address constant UNI  = 0xb4CB800910B228ED3d0834cF79D697127BBB00e5;
    uint160 constant MIN_SQRT = 4295128740;
    uint160 constant MAX_SQRT = 1461446703485210103287273052203988822378723970341;

    function run(uint256 amountUSDC,uint256 minProfit) external returns (uint256 profit) {
        uint256 beforeBal=IERC20(USDC).balanceOf(address(this));
        require(beforeBal>=amountUSDC,"capital");
        (int256 a0,int256 a1)=IV3Pool(PAN).swap(address(this),false,int256(amountUSDC),MAX_SQRT,bytes(""));
        require(a0<0 && a1>0,"pan");
        uint256 wethOut=uint256(-a0);
        (int256 b0,int256 b1)=IV3Pool(UNI).swap(address(this),true,int256(wethOut),MIN_SQRT,bytes(""));
        require(b0>0 && b1<0,"uni");
        uint256 finalBal=IERC20(USDC).balanceOf(address(this));
        profit=finalBal-beforeBal;
        require(profit>=minProfit,"no-profit");
    }
    function pancakeV3SwapCallback(int256 amount0Delta,int256 amount1Delta,bytes calldata) external {
        require(msg.sender==PAN,"cb-pan");
        if(amount0Delta>0) require(IERC20(WETH).transfer(msg.sender,uint256(amount0Delta)));
        if(amount1Delta>0) require(IERC20(USDC).transfer(msg.sender,uint256(amount1Delta)));
    }
    function uniswapV3SwapCallback(int256 amount0Delta,int256 amount1Delta,bytes calldata) external {
        require(msg.sender==UNI,"cb-uni");
        if(amount0Delta>0) require(IERC20(WETH).transfer(msg.sender,uint256(amount0Delta)));
        if(amount1Delta>0) require(IERC20(USDC).transfer(msg.sender,uint256(amount1Delta)));
    }
}

contract BaseArbTest is Test {
    address constant USDC=0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913;

    function testCapturedBlock() public {
        AtomicTwoPoolArb x=new AtomicTwoPoolArb();
        deal(USDC,address(x),125e6);
        uint256 b0=IERC20(USDC).balanceOf(address(x));
        uint256 g0=gasleft();
        uint256 p=x.run(125e6,1);
        uint256 used=g0-gasleft();
        uint256 b1=IERC20(USDC).balanceOf(address(x));
        emit log_named_uint("initial_usdc_raw",b0);
        emit log_named_uint("final_usdc_raw",b1);
        emit log_named_uint("profit_usdc_raw",p);
        emit log_named_uint("executor_call_gas",used);
        assertEq(b1-b0,p);
        assertGt(p,0);
    }

    function testProfitCurve() public {
        uint256[12] memory amts=[uint256(1e6),5e6,10e6,25e6,50e6,75e6,100e6,125e6,150e6,200e6,250e6,300e6];
        for(uint i=0;i<amts.length;i++){
            AtomicTwoPoolArb x=new AtomicTwoPoolArb();
            deal(USDC,address(x),amts[i]);
            uint256 g0=gasleft();
            try x.run(amts[i],0) returns(uint256 p) {
                uint256 used=g0-gasleft();
                emit log_named_uint("amount_usdc_raw",amts[i]);
                emit log_named_uint("profit_usdc_raw",p);
                emit log_named_uint("gas",used);
            } catch {
                emit log_named_uint("reverted_amount_usdc_raw",amts[i]);
            }
        }
    }
}
