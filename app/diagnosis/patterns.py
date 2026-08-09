"""可替换的通用工业故障模式定义。

这些模式来自常见工业测点关系，只用于在企业知识库缺失时提供初步排查顺序，不代表任何
特定设备的确诊规则。后续获得联通或赛题企业的设备手册、拓扑、告警规则后，可以新增外部
模式加载器，而根因排序、API、页面和工单协议无需改写。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FaultPattern:
    """一个通用故障模式及其证据要求。"""

    pattern_id: str
    name: str
    category: str
    sensor_groups: tuple[str, ...]
    required_groups: tuple[str, ...]
    directional_rules: tuple[tuple[str, str], ...]
    relationship_groups: tuple[tuple[str, str], ...]
    verification_steps: tuple[str, ...]
    missing_information: tuple[str, ...]


BUILTIN_FAULT_PATTERNS = (
    FaultPattern(
        pattern_id="flow_restriction",
        name="阀门卡滞、管路堵塞或出口阻力增大",
        category="流体系统异常",
        sensor_groups=("pressure", "flow", "current"),
        required_groups=("pressure", "flow"),
        directional_rules=(("pressure", "up"), ("flow", "down")),
        relationship_groups=(("pressure", "flow"),),
        verification_steps=(
            "核对事件时刻阀门开度、控制指令和执行器反馈",
            "检查过滤器、阀芯和管路是否存在堵塞或异常阻力",
            "对照泵出口压力、流量和电流判断是否存在机械卡阻",
        ),
        missing_information=("阀门开度与执行器反馈", "泵运行状态", "管路拓扑"),
    ),
    FaultPattern(
        pattern_id="supply_loss_or_leak",
        name="供给不足、泄漏或泵性能下降",
        category="流体系统异常",
        sensor_groups=("pressure", "flow", "current"),
        required_groups=("pressure", "flow"),
        directional_rules=(("pressure", "down"), ("flow", "down")),
        relationship_groups=(("pressure", "flow"),),
        verification_steps=(
            "检查泵入口、出口及连接管路是否泄漏或吸空",
            "核对电机电流、转速和供电是否同步下降",
            "复核压力与流量传感器零点和量程",
        ),
        missing_information=("泵转速", "阀门开度", "现场泄漏检查结果"),
    ),
    FaultPattern(
        pattern_id="mechanical_load_or_jam",
        name="机械负载突增、卡阻或传动部件异常",
        category="机械传动异常",
        sensor_groups=("vibration", "current", "temperature"),
        required_groups=("vibration", "current"),
        directional_rules=(("vibration", "up"), ("current", "up")),
        relationship_groups=(("vibration", "current"),),
        verification_steps=(
            "检查轴承、联轴器、转子和运动部件是否存在卡阻或松动",
            "核对事件时刻负载、转速和启动停止操作",
            "现场复测振动并比较轴向、径向和不同测点响应",
        ),
        missing_information=("设备转速", "负载指令", "轴承与联轴器检查结果"),
    ),
    FaultPattern(
        pattern_id="bearing_or_alignment",
        name="轴承磨损、转子不平衡或联轴器不对中",
        category="机械状态退化",
        sensor_groups=("vibration", "temperature", "current"),
        required_groups=("vibration",),
        directional_rules=(("vibration", "up"),),
        relationship_groups=(),
        verification_steps=(
            "采集更高频振动原始波形并检查特征频率和频带能量",
            "检查轴承温升、润滑状态、安装紧固和联轴器对中",
            "对比两路振动测点判断异常是否具有空间一致性",
        ),
        missing_information=("振动原始波形", "转速", "轴承型号与维护记录"),
    ),
    FaultPattern(
        pattern_id="thermal_overload",
        name="负载过高、散热不足或润滑状态恶化",
        category="热状态异常",
        sensor_groups=("temperature", "current", "vibration"),
        required_groups=("temperature",),
        directional_rules=(("temperature", "up"),),
        relationship_groups=(("temperature", "current"),),
        verification_steps=(
            "核对设备负载、电流和环境温度是否同步升高",
            "检查冷却、通风、润滑和热交换通道",
            "使用独立测温手段复核温度测点",
        ),
        missing_information=("设备允许温度", "环境温度", "冷却与润滑状态"),
    ),
    FaultPattern(
        pattern_id="power_supply_or_electrical",
        name="供电波动、电气连接异常或电机负载变化",
        category="电气系统异常",
        sensor_groups=("voltage", "current"),
        required_groups=("voltage", "current"),
        directional_rules=(),
        relationship_groups=(("voltage", "current"),),
        verification_steps=(
            "检查电源质量、接线端子、驱动器告警和保护记录",
            "核对电压变化是否领先电流和其他工艺变量",
            "使用独立仪表复核电压与电流测量链路",
        ),
        missing_information=("驱动器日志", "电源质量记录", "电机额定参数"),
    ),
    FaultPattern(
        pattern_id="sensor_or_acquisition",
        name="传感器漂移、安装松动或采集链路异常",
        category="测量系统异常",
        sensor_groups=(
            "vibration",
            "current",
            "pressure",
            "temperature",
            "voltage",
            "flow",
        ),
        required_groups=(),
        directional_rules=(),
        relationship_groups=(),
        verification_steps=(
            "核查主导测点量程、接线、供电、采样频率和时间同步",
            "使用独立仪表或冗余测点复测异常变量",
            "检查异常是否只出现在单一测点而相邻变量保持稳定",
        ),
        missing_information=("传感器型号与量程", "校准记录", "冗余测点或独立复测结果"),
    ),
    FaultPattern(
        pattern_id="multivariable_process_change",
        name="多变量工艺耦合变化或未记录的操作事件",
        category="工况与控制异常",
        sensor_groups=(
            "vibration",
            "current",
            "pressure",
            "temperature",
            "voltage",
            "flow",
        ),
        required_groups=(),
        directional_rules=(),
        relationship_groups=(),
        verification_steps=(
            "对齐事件时刻的负载、阀门动作、启停操作和控制指令",
            "检查多个测点是否按工艺链路顺序传播",
            "确认该变化是否为新的正常工况并回写事件标签",
        ),
        missing_information=("控制指令日志", "生产计划与负载记录", "设备与工艺拓扑"),
    ),
)


SENSOR_GROUP_KEYWORDS = {
    "vibration": ("accelerometer", "vibration", "振动", "加速度"),
    "current": ("current", "ampere", "电流"),
    "pressure": ("pressure", "压力"),
    "temperature": ("temperature", "thermocouple", "temp", "温度", "热电偶"),
    "voltage": ("voltage", "volt", "电压"),
    "flow": ("flow", "流量"),
}


def classify_sensor(sensor_name: str) -> str:
    """把中英文测点名映射到通用物理量类别，未知字段保持为 other。"""

    normalized = sensor_name.lower().replace(" ", "")
    for group, keywords in SENSOR_GROUP_KEYWORDS.items():
        if any(keyword in normalized for keyword in keywords):
            return group
    return "other"
