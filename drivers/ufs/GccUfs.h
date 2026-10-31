// SPDX-License-Identifier: BSD-2-Clause-Patent
// SM8250 (kona) GCC and UFS windows used by NativeUfs.
//
// This is the enuma counterpart of UniXNU/ShengUfs GccUfs.h (SM8550). Every value below is
// transcribed from the LineageOS kernel tree for this SoC (the source of record for this port);
// nothing is guessed:
//
//   drivers/clk/qcom/gcc-kona.c          branch/clk_branch definitions, .enable_reg below
//   arch/arm64/boot/dts/vendor/qcom/kona.dtsi:3069   ufshc@1d84000 reg/reg-names/interrupts
//   the merged enuma device tree (sha256 296c7e91...):
//     ufshc@1d84000       reg = <0x1d84000 0x3000>, <0x1d90000 0x8000>
//     ufsphy_mem@1d87000  reg = <0x1d87000 0xe00>, <0x1d90000 0x8000>
//     qcom,gdsc@177004    ufs_phy_gdsc
//
// As in the SM8550 header, NativeUfs only READS these: the controller and the PHY are brought up
// by the bootloader, which on this tablet has just read boot.img from the same UFS.
#ifndef NATIVE_UFS_GCC_UFS_H
#define NATIVE_UFS_GCC_UFS_H

// Both windows come from the board DT: GCC (clock-controller@100000) and the UFS host controller.
#define GCC_BASE                      0x00100000ULL
#define UFS_HC_BASE                   0x01d84000ULL
#define UFS_HC_SIZE                   0x3000ULL

// UFS ICE / PHY windows, for diagnostics only today.
#define UFS_ICE_BASE                  0x01d90000ULL
#define UFS_ICE_SIZE                  0x8000ULL
#define UFS_PHY_BASE                  0x01d87000ULL
#define UFS_PHY_SIZE                  0x0e00ULL

// ufs_phy_gdsc lives in the "gcc_ufs_phy" register block at 0x177004 on kona, i.e. NOT inside the
// 0x77000 page that holds the branch registers (on SM8550 it was 0x77004 and did fit). The driver
// maps this page separately, so the offsets are split out explicitly.
#define GCC_UFS_PHY_GDSCR_WINDOW      0x177000ULL
#define GCC_UFS_PHY_GDSCR_OFFSET      0x004U
#define GCC_UFS_PHY_GDSCR             (GCC_UFS_PHY_GDSCR_WINDOW + GCC_UFS_PHY_GDSCR_OFFSET)

// kona has no separate "UFS memory PHY" GDSC (SM8550 had one at 0x9e000); the same UFS PHY GDSC
// powers both. The diagnostic prints it twice rather than mapping a window that does not exist.
#define GCC_UFS_MEM_PHY_GDSCR_WINDOW  GCC_UFS_PHY_GDSCR_WINDOW
#define GCC_UFS_MEM_PHY_GDSCR_OFFSET  GCC_UFS_PHY_GDSCR_OFFSET

// Branch gates, all within the 0x77000 page. Source: gcc-kona.c clk_branch definitions.
#define GCC_BRANCH_WINDOW             0x77000ULL
#define GCC_BRANCH_ENABLE_BIT         0x1U

#define GCC_UFS_PHY_AXI_ENABLE        0x77010  // gcc_ufs_phy_axi_clk        (halt_check BRANCH_HALT)
#define GCC_UFS_PHY_AHB_ENABLE        0x77018  // gcc_ufs_phy_ahb_clk
#define GCC_UFS_PHY_TX_SYMBOL0_ENABLE 0x7701c  // gcc_ufs_phy_tx_symbol_0_clk
#define GCC_UFS_PHY_RX_SYMBOL0_ENABLE 0x77020  // gcc_ufs_phy_rx_symbol_0_clk
#define GCC_UFS_PHY_UNIPRO_CORE_ENABLE 0x7705c // gcc_ufs_phy_unipro_core_clk
#define GCC_UFS_PHY_ICE_CORE_ENABLE   0x77064  // gcc_ufs_phy_ice_core_clk
#define GCC_UFS_PHY_PHY_AUX_ENABLE    0x7709c  // gcc_ufs_phy_phy_aux_clk
#define GCC_UFS_PHY_RX_SYMBOL1_ENABLE 0x770b8  // gcc_ufs_phy_rx_symbol_1_clk
#define GCC_AGGRE_UFS_PHY_AXI_ENABLE  0x770cc  // gcc_aggre_ufs_phy_axi_clk

// vbIf the branch sits behind hardware clock gating, CLK_OFF (bit31) reads as 1 on an idle branch;
// the enable bit (bit0) is what says the branch is on. Same rule as the SM8550 header.
#endif