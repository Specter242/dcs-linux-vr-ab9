-- Linux MOZA AB9 aircraft-profile selector and telemetry exporter.
--
-- Aircraft changes go to UDP 34399.  Flight telemetry goes to UDP 34400 at
-- no more than 50 Hz.  The field names intentionally match MOZA Cockpit's
-- bundled DCS exporter so copied Cockpit profiles remain meaningful.

-- Match MOZA Cockpit's exporter: DCS ships LuaSocket beneath the process
-- working directory, but it is not reliably present in Export.lua's defaults.
package.path = package.path .. ";.\\LuaSocket\\?.lua"
package.cpath = package.cpath .. ";.\\LuaSocket\\?.dll"

local AB9Previous = {}
AB9Previous.LuaExportStart = LuaExportStart
AB9Previous.LuaExportAfterNextFrame = LuaExportAfterNextFrame
AB9Previous.LuaExportStop = LuaExportStop

local ab9SocketModule = nil
local ab9ProfileSocket = nil
local ab9TelemetrySocket = nil
local ab9LastAircraft = nil
local ab9LastAircraftSend = 0
local ab9LastTelemetrySend = 0
local AB9_TELEMETRY_INTERVAL = 0.02
local ab9SocketErrorLogged = false
local ab9FirstFrameLogged = false

local function ab9Log(message)
    if type(log) == "table" and type(log.write) == "function" then
        pcall(log.write, "AB9_EXPORT", log.INFO, tostring(message))
    end
end

local function ab9OpenSockets()
    if not ab9SocketModule then
        local ok, module = pcall(require, "socket")
        if not ok then
            if not ab9SocketErrorLogged then
                ab9Log("LuaSocket import failed: " .. tostring(module))
                ab9SocketErrorLogged = true
            end
            return
        end
        ab9SocketModule = module
        ab9Log("LuaSocket loaded; opening localhost UDP exporters")
    end
    if not ab9ProfileSocket then
        ab9ProfileSocket = ab9SocketModule.udp()
        ab9ProfileSocket:setpeername("127.0.0.1", 34399)
        ab9ProfileSocket:settimeout(0)
    end
    if not ab9TelemetrySocket then
        ab9TelemetrySocket = ab9SocketModule.udp()
        ab9TelemetrySocket:setpeername("127.0.0.1", 34400)
        ab9TelemetrySocket:settimeout(0)
    end
end

local function ab9Now()
    local ok, value = pcall(LoGetModelTime)
    if ok and type(value) == "number" then
        return value
    end
    return os.clock()
end

local function ab9CleanText(value)
    return tostring(value or ""):gsub("[\r\n\t,;]", " ")
end

local function ab9Add(fields, name, value)
    if type(value) == "number" then
        if value == value and value ~= math.huge and value ~= -math.huge then
            fields[#fields + 1] = name .. "," .. string.format("%.6f", value) .. ";"
        end
    elseif type(value) == "boolean" then
        fields[#fields + 1] = name .. "," .. (value and "1" or "0") .. ";"
    elseif value ~= nil then
        fields[#fields + 1] = name .. "," .. ab9CleanText(value) .. ";"
    end
end

local function ab9Call(functionValue, ...)
    local ok, value = pcall(functionValue, ...)
    if ok then
        return value
    end
    return nil
end

local function ab9NestedValue(container, key)
    if type(container) ~= "table" then
        return nil
    end
    local value = container[key]
    if type(value) == "table" then
        return value.value
    end
    return value
end

local function ab9SelfData()
    return ab9Call(LoGetSelfData)
end

-- These module-specific gauge mappings are the same ones shipped in MOZA
-- Cockpit's DCS MainRotorRPM.lua.  Modules without a mapping fall back to
-- LoGetEngineInfo().RPM in the Linux effect daemon.
local function ab9MainRotorRPM(aircraft)
    local panel = ab9Call(GetDevice, 0)
    if type(panel) ~= "table" and type(panel) ~= "userdata" then
        return nil
    end
    pcall(function() panel:update_arguments() end)
    if aircraft == "Mi-8MT" then
        return panel:get_argument_value(42) * 220
    elseif aircraft == "UH-1H" then
        return panel:get_argument_value(123) * 360
    elseif string.find(aircraft or "", "Ka-50", 1, true) then
        return panel:get_argument_value(52) * 350
    elseif aircraft == "Mi-24P" then
        return panel:get_argument_value(42) / 0.95 * 240
    end
    return nil
end

local function ab9ReportAircraft(selfData, now)
    if not ab9ProfileSocket then
        return
    end
    local aircraft = ""
    if type(selfData) == "table" and selfData.Name then
        aircraft = ab9CleanText(selfData.Name)
    end
    if aircraft ~= ab9LastAircraft or now - ab9LastAircraftSend >= 5 then
        ab9ProfileSocket:send("AB9_AIRCRAFT\t" .. aircraft)
        ab9LastAircraft = aircraft
        ab9LastAircraftSend = now
    end
end

local function ab9TelemetryFrame(selfData)
    local fields = {}
    if type(selfData) == "table" then
        ab9Add(fields, "aircraft_name", selfData.Name)
        ab9Add(fields, "heading", selfData.Heading)
        ab9Add(fields, "pitch", selfData.Pitch)
        ab9Add(fields, "bank", selfData.Bank)
    end

    local engineInfo = ab9Call(LoGetEngineInfo)
    if type(engineInfo) == "table" and type(engineInfo.RPM) == "table" then
        ab9Add(fields, "engine_rpm_left", engineInfo.RPM.left)
        ab9Add(fields, "engine_rpm_right", engineInfo.RPM.right)
    end
    if type(selfData) == "table" then
        ab9Add(fields, "helicopter_rotor_rpm", ab9Call(ab9MainRotorRPM, selfData.Name))
    end

    ab9Add(fields, "left_gear", ab9Call(LoGetAircraftDrawArgumentValue, 6))
    ab9Add(fields, "nose_gear", ab9Call(LoGetAircraftDrawArgumentValue, 1))
    ab9Add(fields, "right_gear", ab9Call(LoGetAircraftDrawArgumentValue, 4))

    local acceleration = ab9Call(LoGetAccelerationUnits)
    if type(acceleration) == "table" then
        ab9Add(fields, "acc_x", acceleration.x)
        ab9Add(fields, "acc_y", acceleration.y)
        ab9Add(fields, "acc_z", acceleration.z)
    end

    local wind = ab9Call(LoGetVectorWindVelocity)
    if type(wind) == "table" then
        ab9Add(fields, "wind_x", wind.x)
        ab9Add(fields, "wind_y", wind.y)
        ab9Add(fields, "wind_z", wind.z)
    end

    local velocity = ab9Call(LoGetVectorVelocity)
    if type(velocity) == "table" then
        ab9Add(fields, "vector_velocity_x", velocity.x)
        ab9Add(fields, "vector_velocity_y", velocity.y)
        ab9Add(fields, "vector_velocity_z", velocity.z)
    end

    ab9Add(fields, "tas", ab9Call(LoGetTrueAirSpeed))
    ab9Add(fields, "ias", ab9Call(LoGetIndicatedAirSpeed))
    ab9Add(fields, "vertical_velocity_speed", ab9Call(LoGetVerticalVelocity))
    ab9Add(fields, "aoa", ab9Call(LoGetAngleOfAttack))
    ab9Add(fields, "aos", ab9Call(LoGetAngleOfSideSlip))

    local angularVelocity = ab9Call(LoGetAngularVelocity)
    if type(angularVelocity) == "table" then
        ab9Add(fields, "euler_vx", angularVelocity.x)
        ab9Add(fields, "euler_vy", angularVelocity.y)
        ab9Add(fields, "euler_vz", angularVelocity.z)
    end

    local mechInfo = ab9Call(LoGetMechInfo)
    if type(mechInfo) == "table" then
        ab9Add(fields, "canopy_pos", ab9NestedValue(mechInfo, "canopy"))
        ab9Add(fields, "flap_pos", ab9NestedValue(mechInfo, "flaps"))
        ab9Add(fields, "gear_value", ab9NestedValue(mechInfo, "gear"))
        ab9Add(fields, "speedbrake_value", ab9NestedValue(mechInfo, "speedbrakes"))
        ab9Add(fields, "wheelbrakes_pos", ab9NestedValue(mechInfo, "wheelbrakes"))
    end

    ab9Add(fields, "afterburner_1", ab9Call(LoGetAircraftDrawArgumentValue, 28))
    ab9Add(fields, "afterburner_2", ab9Call(LoGetAircraftDrawArgumentValue, 29))
    ab9Add(fields, "mach", ab9Call(LoGetMachNumber))
    ab9Add(fields, "h_above_sea_level", ab9Call(LoGetAltitudeAboveSeaLevel))
    ab9Add(fields, "h_above_ground_level", ab9Call(LoGetAltitudeAboveGroundLevel))

    local payload = ab9Call(LoGetPayloadInfo)
    if type(payload) == "table" then
        if type(payload.Cannon) == "table" then
            ab9Add(fields, "cannon_shells", payload.Cannon.shells)
        end
        local payloadCount = 0
        if type(payload.Stations) == "table" then
            for _, station in pairs(payload.Stations) do
                if type(station) == "table" and type(station.count) == "number" then
                    payloadCount = payloadCount + station.count
                end
            end
        end
        ab9Add(fields, "payload_count", payloadCount)
    end

    local countermeasures = ab9Call(LoGetSnares)
    if type(countermeasures) == "table" then
        ab9Add(fields, "flare", countermeasures.flare)
        ab9Add(fields, "chaff", countermeasures.chaff)
    end
    return table.concat(fields)
end

local function ab9ReportTelemetry(selfData, now)
    if not ab9TelemetrySocket then
        return
    end
    if now < ab9LastTelemetrySend or now - ab9LastTelemetrySend >= AB9_TELEMETRY_INTERVAL then
        ab9TelemetrySocket:send("AB9_TELEMETRY\t" .. ab9TelemetryFrame(selfData))
        ab9LastTelemetrySend = now
    end
end

function LuaExportStart()
    ab9Log("LuaExportStart")
    ab9OpenSockets()
    if AB9Previous.LuaExportStart then
        AB9Previous.LuaExportStart()
    end
end

function LuaExportAfterNextFrame()
    ab9OpenSockets()
    local now = ab9Now()
    local selfData = ab9SelfData()
    ab9ReportAircraft(selfData, now)
    ab9ReportTelemetry(selfData, now)
    if not ab9FirstFrameLogged and ab9TelemetrySocket then
        ab9Log("first telemetry frame sent")
        ab9FirstFrameLogged = true
    end
    if AB9Previous.LuaExportAfterNextFrame then
        AB9Previous.LuaExportAfterNextFrame()
    end
end

function LuaExportStop()
    if ab9TelemetrySocket then
        pcall(function() ab9TelemetrySocket:send("AB9_STOP\t1") end)
        pcall(function() ab9TelemetrySocket:close() end)
        ab9TelemetrySocket = nil
    end
    if ab9ProfileSocket then
        pcall(function() ab9ProfileSocket:close() end)
        ab9ProfileSocket = nil
    end
    if AB9Previous.LuaExportStop then
        AB9Previous.LuaExportStop()
    end
end
